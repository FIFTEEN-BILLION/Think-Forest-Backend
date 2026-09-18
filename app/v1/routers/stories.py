"""v1 나의 책장 — 완성한 이야기 목록·상세, 고쳐 쓰기, 아끼는 기록 표시, 지우기."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import cursor, library_books
from ..deps import CurrentUser, require_user
from ..library_common import check_version, child_text, own_story, story_summary
from ..library_schemas import (
    SourceConversation,
    StoryDetail,
    StoryEdited,
    StoryPatchRequest,
    WordUsed,
)
from ..models_conversation import ConversationMessage, ConversationSession, StoryRecord
from ..models_library import WordbookEntry
from ..schemas_conversation import (
    FavoriteResponse,
    FavoriteState,
    StoryList,
    TopicCategory,
    TopicRef,
)
from ..story_engine import story_out

router = APIRouter(prefix="/stories", tags=["v1-stories"])


def _kst_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min) - timedelta(hours=9)


def _own_story(db: Session, cu: CurrentUser, story_id: str) -> StoryRecord:
    return own_story(db, cu.id, story_id)


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
        items=[story_summary(s) for s in page],
        next_cursor=cursor.encode(page[-1].updated_at, page[-1].id) if len(rows) > size else None,
    )


def _words_used(db: Session, cu: CurrentUser, story: StoryRecord) -> list[WordUsed]:
    """이 이야기에서 담은 낱말 — 원본 대화에서 담았거나, 이야기 글에 실제로 나오는 낱말."""
    text = f"{story.title} {story.summary} {story.body}"
    rows = db.scalars(
        select(WordbookEntry).where(WordbookEntry.user_id == cu.id).order_by(WordbookEntry.created_at)
    )
    return [
        WordUsed(id=e.id, word=e.word, meaning=e.meaning, status=e.status)  # type: ignore[arg-type]
        for e in rows
        if e.source_conversation_id == story.session_id or e.word in text
    ]


def _source_conversation(db: Session, story: StoryRecord) -> SourceConversation | None:
    session = db.get(ConversationSession, story.session_id)
    if session is None:
        return None
    count = db.scalar(select(func.count(ConversationMessage.id)).where(ConversationMessage.session_id == session.id))
    return SourceConversation(
        conversation_id=session.id,
        topic=TopicRef(id=story.topic_id, title=story.topic_title, category=story.category),
        status=session.status,
        message_count=int(count or 0),
        started_at=cursor.iso(session.created_at) or "",
        completed_at=cursor.iso(session.completed_at),
    )


@router.get("/{story_id}", response_model=StoryDetail)
def get_story(story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    story = _own_story(db, cu, story_id)
    return StoryDetail(
        story=story_out(story),
        words_used=_words_used(db, cu, story),
        source_conversation=_source_conversation(db, story),
    )


@router.patch("/{story_id}", response_model=StoryEdited)
def edit_story(
    story_id: str,
    req: StoryPatchRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    """아이가 자기 말로 고쳐 쓴다. AI 정리본 원본(`ai_original`)은 그대로 둔다."""
    story = _own_story(db, cu, story_id)
    check_version(story.version, if_match, req.version)

    def screen(text: str, limit: int) -> str:
        return child_text(db, cu, text, limit)

    if req.title is not None:
        story.title = screen(req.title, 80)
    if req.summary is not None:
        story.summary = screen(req.summary, 500)
    if req.body is not None:
        story.body = screen(req.body, 4000)
    if req.thought_journey is not None:
        journey = dict(story.thought_journey or {})
        patch = req.thought_journey
        if patch.initial_idea is not None:
            journey["initialIdea"] = screen(patch.initial_idea, 300)
        if patch.final_reflection is not None:
            journey["finalReflection"] = screen(patch.final_reflection, 300)
        if patch.evidence is not None:
            journey["evidence"] = [screen(line, 300) for line in patch.evidence]
        if patch.alternatives is not None:
            journey["alternatives"] = [screen(line, 300) for line in patch.alternatives]
        story.thought_journey = journey  # JSON 컬럼은 새 객체로 바꿔 넣는다
    story.version += 1
    story.updated_at = clock.now()
    db.commit()
    return StoryEdited(story=story_out(story), edited=story.version > 1)


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


@router.delete("/{story_id}", status_code=204)
def delete_story(story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    """이야기를 지운다. 책에서는 빠지지만 같은 책의 다른 이야기는 그대로 있다."""
    story = _own_story(db, cu, story_id)
    library_books.detach_story(db, story.id)
    session = db.get(ConversationSession, story.session_id)
    if session is not None:
        session.story_id = None  # 지운 이야기를 대화가 계속 가리키지 않게 한다
    # TODO(B3 공유): 이 이야기로 게시된 공유가 있으면 함께 내린다(shared_stories·community_posts).
    db.delete(story)
    db.commit()
    return Response(status_code=204)
