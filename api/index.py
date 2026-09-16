"""Vercel 서버리스 진입점. app/main.py 의 FastAPI 인스턴스를 그대로 감싼다."""

from app.main import app

__all__ = ["app"]
