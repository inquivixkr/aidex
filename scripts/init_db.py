"""
DB 초기화 스크립트. 최초 1회 실행.
실행: python scripts/init_db.py
또는: docker compose exec web python scripts/init_db.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Base, engine

Base.metadata.create_all(bind=engine)
print("DB 초기화 완료: data/aidex.db")
