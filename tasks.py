"""
Celery 비동기 작업 (모든 내부 호출은 동기 함수):
- collect_all_blogs: 30분마다 실행. RSS 체크 → 신규 글 감지 → IndexNow 제출
- check_index_status: 24시간마다. Google 인덱싱 상태 확인
"""
import json
from datetime import datetime

from celery import Celery

from config import REDIS_URL, GOOGLE_API_KEY, GOOGLE_CSE_ID, DOMAIN
from models import SessionLocal, Blog, Post
from collector import collect_rss, extract_keywords, classify_category
from indexing import submit_indexnow, check_google_indexed


# ═══════════════════════════════════════════
# Celery 설정
# ═══════════════════════════════════════════

app = Celery("aidex", broker=REDIS_URL)

app.conf.beat_schedule = {
    "collect-every-30min": {
        "task": "tasks.collect_all_blogs",
        "schedule": 1800.0,  # 30분
    },
    "check-index-daily": {
        "task": "tasks.check_index_status",
        "schedule": 86400.0,  # 24시간
    },
}
app.conf.timezone = "UTC"


# ═══════════════════════════════════════════
# 30분마다: RSS 수집 + IndexNow 제출
# ═══════════════════════════════════════════

@app.task(name="tasks.collect_all_blogs")
def collect_all_blogs():
    """
    모든 활성 블로그의 RSS를 체크하여 신규 글을 수집하고,
    새 글이 있으면 IndexNow에 제출합니다.
    모든 함수가 동기이므로 asyncio.run() 호출 없음.
    """
    db = SessionLocal()
    try:
        blogs = db.query(Blog).filter(Blog.status == "active").all()
        new_urls = []

        for blog in blogs:
            # 1단계: RSS 수집
            posts_data = collect_rss(blog.naver_blog_id)

            # RSS 비활성화 감지
            if not posts_data and blog.rss_enabled:
                blog.rss_enabled = False
                blog.status = "rss_disabled"
                db.commit()
                print(f"RSS 비활성화 감지: {blog.naver_blog_id}")
                continue

            for pd in posts_data:
                # 중복 체크
                existing = db.query(Post).filter(
                    Post.blog_id == blog.id,
                    Post.naver_post_id == pd["post_id"],
                ).first()
                if existing:
                    continue

                # AI 키워드 추출 + 카테고리 분류
                keywords = extract_keywords(pd["title"], pd["summary"])
                category = classify_category(pd["title"], pd["summary"])

                # 신규 글 저장
                post = Post(
                    blog_id=blog.id,
                    naver_post_id=pd["post_id"],
                    title=pd["title"],
                    summary=pd["summary"],
                    mobile_url=pd["link"],
                    page_url=f"/blog/{blog.naver_blog_id}/{pd['post_id']}",
                    published_at=pd["published"],
                    source=pd["source"],
                    index_status="pending",
                    ai_keywords=json.dumps(keywords, ensure_ascii=False),
                    ai_category=category,
                )
                db.add(post)

                full_url = f"https://{DOMAIN}/blog/{blog.naver_blog_id}/{pd['post_id']}"
                new_urls.append(full_url)

            blog.last_collected_at = datetime.utcnow().isoformat()

        db.commit()

        # IndexNow 제출 (동기 함수 직접 호출)
        if new_urls:
            success = submit_indexnow(new_urls)
            if success:
                # submitted 상태로 업데이트
                for url in new_urls:
                    parts = url.split("/")
                    post_id_str = parts[-1]
                    blog_id_str = parts[-2]

                    _blog = db.query(Blog).filter(
                        Blog.naver_blog_id == blog_id_str
                    ).first()
                    if _blog:
                        _post = db.query(Post).filter(
                            Post.blog_id == _blog.id,
                            Post.naver_post_id == post_id_str,
                        ).first()
                        if _post:
                            _post.index_status = "submitted"

                db.commit()

        print(f"수집 완료: {len(new_urls)}개 신규 글")
        return {"new_posts": len(new_urls)}

    except Exception as e:
        db.rollback()
        print(f"수집 오류: {e}")
        raise
    finally:
        db.close()


# ═══════════════════════════════════════════
# 24시간마다: 인덱싱 상태 확인
# ═══════════════════════════════════════════

@app.task(name="tasks.check_index_status")
def check_index_status():
    """
    submitted 상태인 글의 Google 인덱싱 여부를 확인합니다 (동기 함수).
    7일 이상 미인덱싱 시 failed 상태로 전환.
    """
    db = SessionLocal()
    try:
        posts = db.query(Post).filter(Post.index_status == "submitted").all()
        checked = 0

        for post in posts:
            blog = db.query(Blog).filter(Blog.id == post.blog_id).first()
            if not blog:
                continue

            page_url = f"https://{DOMAIN}/blog/{blog.naver_blog_id}/{post.naver_post_id}"

            is_indexed = check_google_indexed(
                page_url, GOOGLE_API_KEY, GOOGLE_CSE_ID
            )

            if is_indexed:
                post.index_status = "indexed"
            else:
                post.retry_count += 1
                if post.retry_count >= 7:  # 7일 이상 미인덱싱
                    post.index_status = "failed"

            checked += 1

        db.commit()
        print(f"인덱싱 확인 완료: {checked}개 체크")
        return {"checked": checked}

    except Exception as e:
        db.rollback()
        print(f"인덱싱 확인 오류: {e}")
        raise
    finally:
        db.close()
