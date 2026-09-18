"""홈의 선택 구역(최근 단어·친구 이야기) 읽기.

단어 보관함과 친구 이야기 테이블은 다른 트랙이 만든다. 아직 없을 수도 있으므로 클래스를 import 하지 않고
런타임 반영(reflection)으로 읽는다. 테이블이나 칼럼이 없으면 빈 목록을 돌려준다(홈은 절대 깨지지 않는다).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, Table, desc, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .schemas_conversation import CommunityStoryPreview, RecentWord

WORDBOOK_TABLES = ("wordbook_entries", "word_entries", "wordbook")
COMMUNITY_TABLES = ("community_stories", "shared_stories", "story_shares")
PREVIEW_LIMIT = 3


def _table(db: Session, names: tuple[str, ...]) -> Table | None:
    try:
        bind = db.get_bind()
        found = next((n for n in names if inspect(bind).has_table(n)), None)
        return Table(found, MetaData(), autoload_with=bind) if found else None
    except SQLAlchemyError:
        return None


def _mine(table: Table, user_id: str):
    """소유자 칼럼이 있으면 내 행만. 없으면 조건 없이 읽는다."""
    return table.c.user_id == user_id if "user_id" in table.c else None


def _newest(table: Table):
    for name in ("created_at", "published_at", "updated_at", "id"):
        if name in table.c:
            return desc(table.c[name])
    return None


def _rows(db: Session, table: Table, where: Any, limit: int) -> list[Any]:
    stmt = select(table).limit(limit)
    if where is not None:
        stmt = stmt.where(where)
    order = _newest(table)
    if order is not None:
        stmt = stmt.order_by(order)
    try:
        return list(db.execute(stmt).mappings())
    except SQLAlchemyError:
        return []


def recent_words(db: Session, user_id: str, limit: int = PREVIEW_LIMIT) -> list[RecentWord]:
    """단어 보관함이 아직 없으면 빈 목록."""
    table = _table(db, WORDBOOK_TABLES)
    if table is None or "word" not in table.c:
        return []
    rows = _rows(db, table, _mine(table, user_id), limit)
    return [RecentWord(word=str(r["word"]), meaning=str(r.get("meaning") or "")) for r in rows if r.get("word")]


def community_stories(db: Session, user_id: str, limit: int = PREVIEW_LIMIT) -> list[CommunityStoryPreview]:
    """친구들의 이야기 미리보기. 내 이야기는 빼고, 테이블이 없으면 빈 목록."""
    table = _table(db, COMMUNITY_TABLES)
    if table is None or "title" not in table.c or "id" not in table.c:
        return []
    where = table.c.user_id != user_id if "user_id" in table.c else None
    rows = _rows(db, table, where, limit)
    return [CommunityStoryPreview(id=str(r["id"]), title=str(r["title"])) for r in rows if r.get("title")]
