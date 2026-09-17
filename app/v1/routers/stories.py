"""v1 나의 책장 — 완성한 이야기 목록·상세, 아끼는 기록 표시."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ...db import get_session
from .. import cursor
from ..deps import CurrentUser, require_user
from ..errors import ApiError
from ..models_conversation import StoryRecord
from ..schemas_conversation import (
    FavoriteResponse,
    FavoriteState,
    StoryDetailResponse,
    StoryList,
    StorySummary,
    TopicCategory,
)
from ..story_engine import story_out

router = APIRouter(prefix="/stories", tags=["v1-stories"])


def _kst_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min) - timedelta(hours=9)


def _own_story(db: Session, cu: CurrentUser, story_id: str) -> StoryRecord:
    story = db.get(StoryRecord, story_id)
    if story is None or story.user_id != cu.id:
        raise ApiError(404, "STORY_NOT_FOUND", "이야기를 찾을 수 없어요.")
    return story


@router.get("", response_model=StoryList)
def list_stories(
    query: str | None = Query(default=None, max_length=40),
    category: TopicCategory | None = Query(default=None),
    favorite: bool | None = Query(default=None),
    from_: date | None = Query(default=None, alias="from", description="KST 날짜(포함)"),
    to: date | None = Query(default=None, description="KST 날짜(포함)"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    stmt = select(StoryRecord).where(StoryRecord.user_id == cu.id)
    if query and query.strip():
        q = query.strip()
        stmt = stmt.where(
            or_(
                StoryRecord.title.contains(q, autoescape=True),
                StoryRecord.summary.contains(q, autoescape=True),
                StoryRecord.body.contains(q, autoescape=True),
            )
        )
    if category:
        stmt = stmt.where(StoryRecord.category == category)
    if favorite is not None:
        stmt = stmt.where(StoryRecord.favorite.is_(favorite))
    if from_:
        stmt = stmt.where(StoryRecord.created_at >= _kst_midnight_utc(from_))
    if to:
        stmt = stmt.where(StoryRecord.created_at < _kst_midnight_utc(to + timedelta(days=1)))
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(or_(StoryRecord.updated_at < at, and_(StoryRecord.updated_at == at, StoryRecord.id < row_id)))
    rows = list(db.scalars(stmt.order_by(StoryRecord.updated_at.desc(), StoryRecord.id.desc()).limit(size + 1)))
    page = rows[:size]
    return StoryList(
        items=[
            StorySummary(
                id=s.id,
                title=s.title,
                summary=s.summary,
                category=s.category,
                favorite=s.favorite,
                version=s.version,
                source_conversation_id=s.session_id,
                created_at=cursor.iso(s.created_at) or "",
                updated_at=cursor.iso(s.updated_at) or "",
            )
            for s in page
        ],
        next_cursor=cursor.encode(page[-1].updated_at, page[-1].id) if len(rows) > size else None,
    )


@router.get("/{story_id}", response_model=StoryDetailResponse)
def get_story(story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    return StoryDetailResponse(story=story_out(_own_story(db, cu, story_id)))


def _set_favorite(db: Session, cu: CurrentUser, story_id: str, value: bool) -> FavoriteResponse:
    # 즐겨찾기는 내용 수정이 아니므로 version·updatedAt 을 올리지 않는다(PUT/DELETE 반복해도 같은 결과).
    story = _own_story(db, cu, story_id)
    story.favorite = value
    db.commit()
    return FavoriteResponse(
        story=FavoriteState(
            id=story.id, favorite=story.favorite, version=story.version, updated_at=cursor.iso(story.updated_at) or ""
        )
    )


@router.put("/{story_id}/favorite", response_model=FavoriteResponse)
def add_favorite(story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    return _set_favorite(db, cu, story_id, True)


@router.delete("/{story_id}/favorite", response_model=FavoriteResponse)
def remove_favorite(story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    return _set_favorite(db, cu, story_id, False)
