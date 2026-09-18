"""v1 티키와 이야기 — 목록·시작·복원·답변·완료·취소."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ...db import get_session
from .. import chat, conversation_scope, cursor, idempotency
from .. import story_engine as engine
from ..deps import CurrentUser, require_user
from ..errors import ApiError
from ..models_conversation import ConversationSession
from ..schemas_conversation import (
    CancelResponse,
    CompleteRequest,
    ConversationCompletion,
    ConversationDetail,
    ConversationList,
    ConversationMessageResponse,
    ConversationStartRequest,
    ConversationStartResponse,
    MessageRequest,
)

router = APIRouter(prefix="/conversations", tags=["v1-conversations"])
STATUSES = ("ACTIVE", "READY_TO_FINISH", "FINALIZING", "COMPLETED", "CANCELLED")


@router.get("", response_model=ConversationList)
def list_conversations(
    status: str | None = Query(default=None, description="쉼표로 여러 개: ACTIVE,READY_TO_FINISH"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    query = select(ConversationSession).where(
        ConversationSession.user_id == cu.id, ConversationSession.kind == engine.KIND,
        conversation_scope.condition(ConversationSession.id, cu.child.id)
    )
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        if not wanted or any(s not in STATUSES for s in wanted):
            raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["status"]})
        query = query.where(ConversationSession.status.in_(wanted))
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        query = query.where(
            or_(
                ConversationSession.updated_at < at,
                and_(ConversationSession.updated_at == at, ConversationSession.id < row_id),
            )
        )
    rows = list(
        db.scalars(query.order_by(ConversationSession.updated_at.desc(), ConversationSession.id.desc()).limit(size + 1))
    )
    page = rows[:size]
    next_cursor = cursor.encode(page[-1].updated_at, page[-1].id) if len(rows) > size else None
    return ConversationList(items=[engine.summary_out(s) for s in page], next_cursor=next_cursor)


@router.post("", response_model=ConversationStartResponse, status_code=201)
def start_conversation(
    req: ConversationStartRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    route = "POST /conversations"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    out = engine.start(db, cu, req.topic_id)
    idempotency.remember(db, cu.id, idempotency_key, route, out, status_code=201)
    db.commit()
    return out


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    message_cursor: str | None = Query(default=None, alias="messageCursor"),
    limit: int | None = Query(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    session = chat.own_session(db, cu, conversation_id, engine.KIND)
    return engine.detail_out(db, session, message_cursor, limit)


@router.post("/{conversation_id}/messages", response_model=ConversationMessageResponse)
def post_message(
    conversation_id: str,
    req: MessageRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    session = chat.own_session(db, cu, conversation_id, engine.KIND)
    if (replayed := idempotency.replay(db, cu.id, req.client_message_id, chat.message_route(session))) is not None:
        return replayed
    chat.ensure_open(session)
    return engine.handle_message(db, cu, session, req)


@router.post("/{conversation_id}/complete", response_model=ConversationCompletion)
def complete_conversation(
    conversation_id: str,
    req: CompleteRequest | None = None,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    session = chat.own_session(db, cu, conversation_id, engine.KIND)
    route = f"POST /conversations/{conversation_id}/complete"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    out = engine.complete(db, cu, session)
    idempotency.remember(db, cu.id, idempotency_key, route, out)
    db.commit()
    return out


@router.post("/{conversation_id}/cancel", response_model=CancelResponse)
def cancel_conversation(
    conversation_id: str,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    session = chat.own_session(db, cu, conversation_id, engine.KIND)
    engine.cancel(session)
    db.commit()
    return CancelResponse(
        conversation_id=session.id, status=session.status, cancelled_at=cursor.iso(session.cancelled_at)
    )
