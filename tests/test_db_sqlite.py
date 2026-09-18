"""develop 통합 이후에도 로컬 SQLite 경로와 빈 DATABASE_URL 계약을 유지한다."""

from pathlib import Path

import pytest
from app import db
from app.config import get_settings
from sqlalchemy import text
from sqlalchemy.pool import StaticPool


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_database_url_uses_thinkforest(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", value)
    get_settings.cache_clear()
    try:
        assert get_settings().database_url == "sqlite:///./data/thinkforest.db"
    finally:
        get_settings.cache_clear()


def test_relative_sqlite_path_is_independent_of_working_directory(monkeypatch, tmp_path):
    # Rebase the module itself to a temporary backend: never open the user's DB.
    backend = tmp_path / "backend"
    (backend / "app").mkdir(parents=True)
    monkeypatch.setattr(db, "__file__", str(backend / "app" / "db.py"))
    monkeypatch.chdir(tmp_path)
    engine = db.configure("sqlite:///./data/thinkforest.db")
    try:
        assert Path(engine.url.database) == backend / "data" / "thinkforest.db"
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE location_test (id INTEGER PRIMARY KEY)"))
        assert (backend / "data" / "thinkforest.db").exists()
        assert not (tmp_path / "data" / "thinkforest.db").exists()
    finally:
        engine.dispose()
        db.configure("sqlite://")


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_memory_database_remains_shared_between_sessions(url):
    engine = db.configure(url)
    try:
        assert isinstance(engine.pool, StaticPool)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE memory_test (value INTEGER)"))
            connection.execute(text("INSERT INTO memory_test VALUES (7)"))
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT value FROM memory_test")) == 7
    finally:
        engine.dispose()
        db.configure("sqlite://")
