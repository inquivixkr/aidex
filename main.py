"""
AI DEX v2.2 — FastAPI 메인 앱
모든 라우트는 aidex.kr/ 아래 서브디렉토리로 동작합니다.
"""
import json

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import joinedload

from config import INDEXNOW_KEY, DOMAIN
from models import SessionLocal, Blog, Post, Base, engine
from indexing import generate_sitemap
from collector import collect_rss, extract_keywords, classify_category


# ═══════════════════════════════════════════
# 앱 초기화
# ═══════════════════════════════════════════

app = FastAPI(title="AI DEX", version="2.2")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def startup():
    """앱 시작 시 DB 테이블 자동 생성"""
    Base.metadata.create_all(bind=engine)


# ═══════════════════════════════════════════
# 대시보드 홈
# ═══════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = SessionLocal()
    try:
        blogs = db.query(Blog).filter(Blog.status == "active").all()
        total_posts = db.query(Post).filter(Post.is_deleted == False).count()
        indexed = db.query(Post).filter(Post.index_status == "indexed").count()
        pending = db.query(Post).filter(Post.index_status == "pending").count()
        submitted = db.query(Post).filter(Post.index_status == "submitted").count()

        return templates.TemplateResponse(request, "dashboard.html", {
            "blogs": blogs,
            "total_posts": total_posts,
            "indexed": indexed,
            "pending": pending,
            "submitted": submitted,
        })
    finally:
        db.close()


# ═══════════════════════════════════════════
# 블로그 글 목록
# ═══════════════════════════════════════════

@app.get("/blog/{blog_id}", response_class=HTMLResponse)
async def blog_index(request: Request, blog_id: str):
    db = SessionLocal()
    try:
        blog = db.query(Blog).filter(Blog.naver_blog_id == blog_id).first()
        if not blog:
            raise HTTPException(404, "블로그를 찾을 수 없습니다")

        posts = db.query(Post).filter(
            Post.blog_id == blog.id,
            Post.is_deleted == False,
        ).order_by(Post.published_at.desc()).all()

        return templates.TemplateResponse(request, "blog_index.html", {
            "blog": blog,
            "posts": posts,
        })
    finally:
        db.close()


# ═══════════════════════════════════════════
# 개별 포스트 페이지 (SEO + GEO 최적화)
# ═══════════════════════════════════════════

@app.get("/blog/{blog_id}/{post_id}", response_class=HTMLResponse)
async def post_page(request: Request, blog_id: str, post_id: str):
    db = SessionLocal()
    try:
        blog = db.query(Blog).filter(Blog.naver_blog_id == blog_id).first()
        if not blog:
            raise HTTPException(404, "블로그를 찾을 수 없습니다")

        post = db.query(Post).filter(
            Post.blog_id == blog.id,
            Post.naver_post_id == post_id,
            Post.is_deleted == False,
        ).first()
        if not post:
            raise HTTPException(404, "글을 찾을 수 없습니다")

        # 관련 포스트 (같은 블로그, 같은 카테고리, 최대 5개)
        related = db.query(Post).filter(
            Post.blog_id == blog.id,
            Post.ai_category == post.ai_category,
            Post.id != post.id,
            Post.is_deleted == False,
        ).limit(5).all()

        keywords = json.loads(post.ai_keywords) if post.ai_keywords else []

        return templates.TemplateResponse(request, "post.html", {
            "blog": blog,
            "post": post,
            "related": related,
            "keywords": keywords,
            "domain": DOMAIN,
        })
    finally:
        db.close()


# ═══════════════════════════════════════════
# 동적 Sitemap (N+1 수정: joinedload)
# ═══════════════════════════════════════════

@app.get("/sitemap.xml")
async def sitemap():
    """
    동적 sitemap.xml 생성.
    Google Search Console에 https://aidex.kr/sitemap.xml 을 등록하세요.
    joinedload로 Blog를 한 번에 로드하여 N+1 쿼리 방지.
    """
    db = SessionLocal()
    try:
        posts = db.query(Post).options(
            joinedload(Post.blog)
        ).filter(Post.is_deleted == False).all()

        post_dicts = [{
            "blog_id": p.blog.naver_blog_id,
            "post_id": p.naver_post_id,
            "published_at": p.published_at or "",
            "is_deleted": p.is_deleted,
        } for p in posts]

        xml = generate_sitemap(post_dicts)
        return Response(content=xml, media_type="application/xml")
    finally:
        db.close()


# ═══════════════════════════════════════════
# robots.txt (AI 크롤러 허용)
# ═══════════════════════════════════════════

@app.get("/robots.txt")
async def robots():
    """AI 크롤러를 명시적으로 허용하는 robots.txt."""
    content = f"""# AI 크롤러 명시적 허용
User-agent: GPTBot
Allow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Applebot-Extended
Allow: /

# 기존 검색엔진
User-agent: *
Allow: /
Disallow: /api/

Sitemap: https://{DOMAIN}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


# ═══════════════════════════════════════════
# IndexNow 키 파일 (안전한 라우트)
# ═══════════════════════════════════════════

@app.get("/indexnow-verify/{key}.txt")
async def indexnow_key_file(key: str):
    """IndexNow 소유권 인증용 키 파일."""
    if not INDEXNOW_KEY or key != INDEXNOW_KEY:
        raise HTTPException(404)
    return Response(content=INDEXNOW_KEY, media_type="text/plain")


@app.get("/{key}.txt")
async def indexnow_key_file_root(key: str):
    """IndexNow 표준 키 파일 경로 (루트)"""
    if not INDEXNOW_KEY or key != INDEXNOW_KEY:
        raise HTTPException(404)
    return Response(content=INDEXNOW_KEY, media_type="text/plain")


# ═══════════════════════════════════════════
# 내부 API: 블로그 추가
# ═══════════════════════════════════════════

@app.post("/api/blogs")
async def add_blog(naver_blog_id: str, client_name: str = ""):
    """블로그를 등록하고 RSS 피드 확인."""
    db = SessionLocal()
    try:
        existing = db.query(Blog).filter(
            Blog.naver_blog_id == naver_blog_id
        ).first()
        if existing:
            return {"error": "이미 등록된 블로그입니다", "blog_id": existing.id}

        # RSS 피드 확인
        test_posts = collect_rss(naver_blog_id)
        rss_enabled = len(test_posts) > 0

        blog = Blog(
            naver_blog_id=naver_blog_id,
            client_name=client_name,
            rss_enabled=rss_enabled,
            status="active" if rss_enabled else "rss_disabled",
        )
        db.add(blog)
        db.commit()
        db.refresh(blog)

        return {
            "blog_id": blog.id,
            "naver_blog_id": naver_blog_id,
            "rss_enabled": rss_enabled,
            "posts_found": len(test_posts),
            "message": (
                "블로그 등록 완료"
                if rss_enabled
                else "RSS 비활성화 상태입니다. 블로그 관리 → 설정 → RSS 공개 설정을 활성화해주세요."
            ),
        }
    finally:
        db.close()


# ═══════════════════════════════════════════
# 내부 API: 수동 수집 트리거
# ═══════════════════════════════════════════

@app.post("/api/collect/{blog_id}")
async def trigger_collect(blog_id: str):
    """수동으로 특정 블로그의 수집을 트리거합니다."""
    db = SessionLocal()
    try:
        blog = db.query(Blog).filter(Blog.naver_blog_id == blog_id).first()
        if not blog:
            raise HTTPException(404, "블로그를 찾을 수 없습니다")

        posts_data = collect_rss(blog.naver_blog_id)
        new_count = 0

        for pd in posts_data:
            existing = db.query(Post).filter(
                Post.blog_id == blog.id,
                Post.naver_post_id == pd["post_id"],
            ).first()
            if existing:
                continue

            keywords = extract_keywords(pd["title"], pd["summary"])
            category = classify_category(pd["title"], pd["summary"])

            post = Post(
                blog_id=blog.id,
                naver_post_id=pd["post_id"],
                title=pd["title"],
                summary=pd["summary"],
                mobile_url=pd["link"],
                page_url=f"/blog/{blog.naver_blog_id}/{pd['post_id']}",
                published_at=pd["published"],
                source=pd["source"],
                index_status="submitted",
                ai_keywords=json.dumps(keywords, ensure_ascii=False),
                ai_category=category,
            )
            db.add(post)
            new_count += 1

        db.commit()
        return {"blog_id": blog_id, "new_posts": new_count, "total_found": len(posts_data)}
    finally:
        db.close()
