"""v1 티키와 첫인사 — 세션 시작(이어하기)·복원·답변·완료."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from ...db import get_session
from .. import chat, idempotency
from .. import greeting_engine as engine
from ..deps import CurrentUser, require_user
from ..schemas_conversation import (
    CompleteRequest,
    GreetingCompletion,
    GreetingMessageResponse,
    GreetingSessionOut,
    MessageRequest,
)

router = APIRouter(prefix="/first-greeting", tags=["v1-first-greeting"])


@router.post("/sessions", response_model=GreetingSessionOut)
def start_session(
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    route = "POST /first-greeting/sessions"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    out = engine.start_or_resume(db, cu)
    idempotency.remember(db, cu.id, idempotency_key, route, out)
    db.commit()
    return out


@router.get("/sessions/{session_id}", response_model=GreetingSessionOut)
def get_session_detail(
    session_id: str,
    message_cursor: str | None = Query(default=None, alias="messageCursor"),
    limit: int | None = Query(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    session = chat.own_session(db, cu, session_id, engine.KIND)
    return engine.session_out(db, session, message_cursor=message_cursor, limit=limit)


@router.post("/sessions/{session_id}/messages", response_model=GreetingMessageResponse)
def post_message(
    session_id: str,
    req: MessageRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    session = chat.own_session(db, cu, session_id, engine.KIND)
    if (replayed := idempotency.replay(db, cu.id, req.client_message_id, chat.message_route(session))) is not None:
        return replayed
    chat.ensure_open(session)
    return engine.handle_message(db, cu, session, req)


@router.post("/sessions/{session_id}/complete", response_model=GreetingCompletion)
def complete_session(
    session_id: str,
    req: CompleteRequest | None = None,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    session = chat.own_session(db, cu, session_id, engine.KIND)
    route = f"POST /first-greeting/sessions/{session_id}/complete"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    out = engine.complete(db, cu, session)
    idempotency.remember(db, cu.id, idempotency_key, route, out)
    db.commit()
    return out
