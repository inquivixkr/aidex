"""
DB 모델 + 엔진 초기화
- SQLite WAL 모드 활성화 (web + worker 동시 접근 시 lock 방지)
- check_same_thread=False (FastAPI 비동기 컨텍스트 대응)
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Text, Integer, Boolean, ForeignKey,
    create_engine, event,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from config import DATABASE_URL

Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


class Blog(Base):
    __tablename__ = "blogs"

    id = Column(String, primary_key=True, default=gen_uuid)
    naver_blog_id = Column(String(50), nullable=False, unique=True)
    client_name = Column(String(100), nullable=True)
    rss_enabled = Column(Boolean, default=True)
    status = Column(String(20), default="active")  # active/paused/error/rss_disabled
    last_collected_at = Column(String, nullable=True)
    collect_interval = Column(Integer, default=30)  # 분 단위
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())

    posts = relationship("Post", back_populates="blog", cascade="all, delete-orphan")


class Post(Base):
    __tablename__ = "posts"

    id = Column(String, primary_key=True, default=gen_uuid)
    blog_id = Column(String, ForeignKey("blogs.id"), nullable=False)
    naver_post_id = Column(String(20), nullable=False)
    title = Column(String(500), nullable=False)
    summary = Column(Text, nullable=True)           # 200자 원본 그대로
    ai_keywords = Column(Text, nullable=True)       # JSON 배열 문자열
    ai_category = Column(String(50), nullable=True)
    mobile_url = Column(String(500), nullable=False)
    page_url = Column(String(500), nullable=True)   # /blog/{blog_id}/{post_id}
    og_image_url = Column(String(500), nullable=True)
    index_status = Column(String(20), default="pending")  # pending/submitted/indexed/failed
    retry_count = Column(Integer, default=0)
    published_at = Column(String, nullable=True)
    source = Column(String(10), default="rss")      # rss/api/manual
    is_deleted = Column(Boolean, default=False)      # soft delete
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())

    blog = relationship("Blog", back_populates="posts")


def get_engine():
    """
    SQLite 엔진 생성 + WAL 모드 활성화.
    WAL 모드는 읽기/쓰기 동시 접근을 허용하여
    web 컨테이너와 worker 컨테이너가 동시에 DB에 접근할 때
    'database is locked' 에러를 방지합니다.
    """
    engine = create_engine(
        DATABASE_URL,
        connect_args={
            "check_same_thread": False,
            "timeout": 15,
        },
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


engine = get_engine()
SessionLocal = sessionmaker(bind=engine)
