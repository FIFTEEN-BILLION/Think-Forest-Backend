"""DB 연결 — SQLAlchemy 2.0.

기본은 SQLite 파일(개발). 운영은 DATABASE_URL 을 PostgreSQL(Supabase)로 바꾼다.
마이그레이션 도구는 아직 없다 — 스키마가 굳으면 Alembic 을 붙인다.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def configure(url: str | None = None) -> Engine:
    global _engine, _factory
    url = (url or "").strip() or get_settings().database_url
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        path = Path(parsed.database)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        parsed = parsed.set(database=str(path))
    url = parsed.render_as_string(hide_password=False)
    kwargs: dict[str, Any] = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if parsed.database in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool
    else:
        # Supabase 등 원격 Postgres — 서버리스 콜드 스타트 사이 끊긴 커넥션을 재사용하지 않도록 확인 후 사용
        kwargs["pool_pre_ping"] = True
        # Supabase PgBouncer(transaction 모드)는 커넥션마다 다른 백엔드로 라우팅한다.
        # psycopg3 가 자동으로 만드는 server-side prepared statement 이름("_pg3_0" 등)이
        # 다른 세션의 것과 충돌해 DuplicatePreparedStatement 를 낸다 — prepare 를 꺼서 회피.
        kwargs["connect_args"] = {"prepare_threshold": None}
    _engine = create_engine(url, **kwargs)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    return _engine or configure()


def init_db() -> None:
    from . import models  # noqa: F401 — 테이블 등록
    from .v1 import tables  # noqa: F401 — v1 테이블 등록

    Base.metadata.create_all(engine())


def get_session() -> Iterator[Session]:
    if _factory is None:
        configure()
    assert _factory is not None
    with _factory() as session:
        yield session
