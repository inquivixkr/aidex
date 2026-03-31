"""
Indexing Engine (모두 동기 함수):
- IndexNow: Bing/Yandex에 즉시 제출 → ChatGPT/Copilot 발견 가능성 증가
- Sitemap: Google + AI 크롤러의 콘텐츠 발견 메인 채널
- Google Custom Search: 인덱싱 상태 확인 (모니터링용)
"""
import json
from datetime import datetime

import httpx

from config import INDEXNOW_KEY, DOMAIN


# ═══════════════════════════════════════════
# IndexNow 제출 (동기)
# ═══════════════════════════════════════════

def submit_indexnow(urls: list[str]) -> bool:
    """
    IndexNow API에 URL 목록을 제출합니다 (동기 함수).
    - 대상: Bing, Yandex, Naver, Seznam (Google은 미지원)
    - 한 번에 최대 10,000개 URL
    - 제출 1회로 모든 참여 검색엔진에 동시 전달
    - Bing에 빠르게 인덱싱 → ChatGPT/Copilot에서 발견 가능성 증가

    반환: True(성공), False(실패)
    """
    if not urls:
        return True

    if not INDEXNOW_KEY:
        print("IndexNow: INDEXNOW_KEY 미설정. 제출 건너뜀.")
        return False

    payload = {
        "host": DOMAIN,
        "key": INDEXNOW_KEY,
        "keyLocation": f"https://{DOMAIN}/{INDEXNOW_KEY}.txt",
        "urlList": urls[:10000],
    }
    headers = {"Content-Type": "application/json; charset=utf-8"}

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                "https://api.indexnow.org/indexnow",
                headers=headers,
                content=json.dumps(payload),
            )
    except httpx.RequestError as e:
        print(f"IndexNow 네트워크 오류: {e}")
        return False

    if resp.status_code in (200, 202):
        print(f"IndexNow 성공: {len(urls)}개 URL 제출 (status={resp.status_code})")
        return True
    else:
        print(f"IndexNow 실패: {resp.status_code} - {resp.text}")
        return False


# ═══════════════════════════════════════════
# Sitemap XML 생성
# ═══════════════════════════════════════════

def generate_sitemap(posts: list[dict]) -> str:
    """
    동적 sitemap.xml을 생성합니다.
    Google + AI 크롤러가 이 파일을 통해 새 페이지를 발견합니다.
    Google Search Console에 https://aidex.kr/sitemap.xml 을 등록해야 합니다.

    posts: [{"blog_id": str, "post_id": str, "published_at": str, "is_deleted": bool}]
    """
    urls_xml = ""
    for post in posts:
        if post.get("is_deleted"):
            continue

        page_url = f"https://{DOMAIN}/blog/{post['blog_id']}/{post['post_id']}"
        lastmod = post.get("published_at", datetime.utcnow().strftime("%Y-%m-%d"))

        # ISO datetime → date only
        if lastmod and "T" in lastmod:
            lastmod = lastmod.split("T")[0]

        urls_xml += f"""  <url>
    <loc>{page_url}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>weekly</changefreq>
  </url>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>"""


# ═══════════════════════════════════════════
# Google 인덱싱 상태 확인 (동기)
# ═══════════════════════════════════════════

def check_google_indexed(page_url: str, api_key: str, cse_id: str) -> bool:
    """
    Google Custom Search API로 인덱싱 여부를 확인합니다 (동기 함수).
    - 무료: 일일 100회
    - site: 쿼리로 도메인 단위 조회
    - submitted 상태인 글만 체크하여 할당량 절약

    반환: True(인덱싱됨), False(미인덱싱 또는 확인 불가)
    """
    if not api_key or not cse_id:
        return False

    post_id = page_url.rstrip("/").split("/")[-1]
    query = f"site:{DOMAIN} {post_id}"

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": api_key, "cx": cse_id, "q": query},
            )
    except httpx.RequestError:
        return False

    if resp.status_code != 200:
        return False

    data = resp.json()
    total = int(data.get("searchInformation", {}).get("totalResults", 0))
    return total > 0
