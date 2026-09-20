"""v1 티키와 첫인사 — 세션 시작(이어하기)·복원·답변·완료."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from ...db import get_session
from .. import ai_gate, chat, idempotency
from .. import greeting_engine as engine
from ..deps import CurrentUser, require_user
from ..schemas_conversation import (
    GreetingCompleteRequest,
    GreetingCompletion,
    GreetingMessageResponse,
    GreetingReadinessResponse,
    GreetingSessionOut,
    MessageRequest,
)

router = APIRouter(prefix="/first-greeting", tags=["v1-first-greeting"])


@router.get("/readiness", response_model=GreetingReadinessResponse)
def greeting_readiness(cu: CurrentUser = Depends(require_user)):
    """외부 AI 호출이나 사용량 소비 없이 첫인사 진입 가능 여부를 알려준다."""
    reason = ai_gate.block_reason(cu.child)
    messages = {
        "guest_consent_required": "보호자가 개인정보와 AI 처리 동의를 확인해 주세요.",
        "child_data_mode_off": "보호자의 AI 처리 동의가 필요해요.",
        "no_api_key": "AI 연결을 준비하고 있어요. 잠시 후 다시 확인해 주세요.",
        "ai_disabled": "지금은 AI 대화를 쉬고 있어요. 잠시 후 다시 확인해 주세요.",
    }
    return {
        "available": reason is None,
        "reason": reason,
        "message": messages.get(reason, "지금은 AI 대화를 시작할 수 없어요.") if reason else None,
    }


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
    req: GreetingCompleteRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    session = chat.own_session(db, cu, session_id, engine.KIND)
    route = f"POST /first-greeting/sessions/{session_id}/complete"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    out = engine.complete(db, cu, session, profile_revision=req.profile_revision)
    idempotency.remember(db, cu.id, idempotency_key, route, out)
    db.commit()
    return out
