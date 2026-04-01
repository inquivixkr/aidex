"""
API Collector: 네이버 블로그 메타데이터 수집
- 1순위: RSS 피드 (메인)
- 2순위: 네이버 Open API 키워드 검색 (보완)

모든 함수는 동기(sync)입니다.
Celery worker에서 직접 호출 가능하도록 통일했습니다.
(asyncio.run() 호출 시 이벤트 루프 충돌 방지)
"""
import re
import json
from collections import Counter

import feedparser
import httpx

from config import NAVER_CLIENT_ID, NAVER_CLIENT_SECRET


# ═══════════════════════════════════════════
# RSS 수집 (메인)
# ═══════════════════════════════════════════

def collect_rss(blog_id: str) -> list[dict]:
    """
    RSS 피드에서 블로그 글 메타데이터를 수집합니다.

    네이버는 2025.12부터 HTTP/1.0을 차단했습니다.
    feedparser 6.x는 기본적으로 urllib(HTTP/1.1)을 사용하므로
    별도 설정 없이 HTTPS로 호출하면 정상 작동합니다.

    반환: [{"title", "summary", "link", "published", "post_id", "source"}]
    빈 리스트 반환 시 → RSS 비활성화 상태로 판정
    """
    feed_url = f"https://rss.blog.naver.com/{blog_id}.xml"
    feed = feedparser.parse(feed_url)

    # RSS 비활성화 감지
    if feed.bozo and not feed.entries:
        return []

    posts = []
    for entry in feed.entries:
        post_id = _extract_post_id(entry.get("link", ""))
        if not post_id:
            continue

        posts.append({
            "title": entry.get("title", "").strip(),
            "summary": _truncate(_strip_html(entry.get("description", "")), 200),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
            "post_id": post_id,
            "source": "rss",
        })

    return posts


# ═══════════════════════════════════════════
# API 키워드 검색 (보완) — 동기 버전
# ═══════════════════════════════════════════

def collect_api(blog_id: str, blog_name: str) -> list[dict]:
    """
    네이버 Open API로 블로그명을 검색하여 과거 글을 보완 수집합니다.

    동기 함수입니다. httpx.Client (동기)를 사용합니다.
    Celery worker에서 asyncio.run() 없이 직접 호출할 수 있습니다.
    """
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        return []

    url = "https://openapi.naver.com/v1/search/blog.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {
        "query": blog_name,
        "display": 100,
        "sort": "date",
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers, params=params)
    except httpx.RequestError:
        return []

    if resp.status_code != 200:
        return []

    data = resp.json()
    posts = []

    for item in data.get("items", []):
        link = item.get("link", "")
        # 해당 blog_id의 글만 필터링
        if f"blog.naver.com/{blog_id}" not in link:
            continue

        post_id = _extract_post_id(link)
        if not post_id:
            continue

        posts.append({
            "title": _strip_html(item.get("title", "")),
            "summary": _truncate(_strip_html(item.get("description", "")), 200),
            "link": link,
            "published": item.get("postdate", ""),
            "post_id": post_id,
            "source": "api",
        })

    return posts


# ═══════════════════════════════════════════
# AI 키워드 추출 (scikit-learn 불필요)
# ═══════════════════════════════════════════

def extract_keywords(title: str, summary: str) -> list[str]:
    """
    제목+요약에서 핵심 키워드 3~5개를 추출합니다.
    한글 2자 이상 단어의 빈도 기반. LLM/scikit-learn 없이 동작.
    Phase 2에서 konlpy/mecab + TfidfVectorizer로 고도화 가능.
    """
    text = f"{title} {summary}"
    words = re.findall(r"[가-힣]{2,}", text)

    stopwords = {
        "것이", "하는", "있는", "있다", "했다", "되는", "우리", "그리고",
        "하지만", "이런", "저런", "그런", "때문", "하고", "에서", "으로",
        "부터", "까지", "에서는", "것을", "합니다", "입니다", "습니다",
    }

    counter = Counter(w for w in words if w not in stopwords)
    return [w for w, _ in counter.most_common(5)]


def classify_category(title: str, summary: str) -> str:
    """
    규칙 기반 카테고리 분류. 제목+요약에서 키워드 매칭.
    매칭되지 않으면 '일반'을 반환합니다.
    """
    text = f"{title} {summary}".lower()

    rules = {
        "맛집": ["맛집", "맛있", "메뉴", "음식", "레스토랑", "카페", "식당", "디저트", "맛"],
        "여행": ["여행", "관광", "호텔", "숙소", "투어", "국내여행", "여행지", "관광지"],
        "IT": ["개발", "프로그래밍", "코딩", "앱", "서버", "클라우드", "ai", "인공지능", "소프트웨어"],
        "육아": ["육아", "아이", "아기", "유아", "교육", "어린이", "임신", "출산"],
        "뷰티": ["화장품", "스킨케어", "메이크업", "뷰티", "피부", "향수"],
        "패션": ["패션", "코디", "옷", "의류", "착장"],
        "건강": ["건강", "운동", "다이어트", "헬스", "영양", "의료", "병원"],
        "리뷰": ["리뷰", "후기", "사용기", "비교", "추천"],
    }

    for category, keywords in rules.items():
        if any(kw in text for kw in keywords):
            return category

    return "일반"


# ═══════════════════════════════════════════
# 유틸리티
# ═══════════════════════════════════════════

def _extract_post_id(url: str) -> str:
    """네이버 블로그 URL에서 post_id를 추출합니다."""
    from urllib.parse import urlparse
    url = urlparse(url).path
    from urllib.parse import urlparse; url = urlparse(url).path; parts = url.rstrip("/").split("/")
    for part in reversed(parts):
        if part.isdigit():
            return part
    return ""


def _truncate(text: str, max_len: int) -> str:
    """텍스트를 최대 길이로 자릅니다."""
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _strip_html(text: str) -> str:
    """HTML 태그를 제거합니다."""
    return re.sub(r"<[^>]+>", "", text).strip()
