"""첫인사·이야기 대화가 함께 쓰는 메시지 처리 — 세션 소유 확인, 메시지 저장·페이지, 질문 형식, 안전 검사, 종료 의도."""

from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import SafetyEvent
from ..safety import pii
from ..safety import topics as sensitive
from ..talks.planner import clip
from . import ai_gate, conversation_scope
from .cursor import iso
from .deps import CurrentUser
from .errors import ApiError
from .models_conversation import ConversationMessage, ConversationSession
from .schemas_conversation import AnswerOut, ChoiceOption, Interaction, MessageOut, MessageRequest

OPEN_STATUSES = ("ACTIVE", "READY_TO_FINISH")
MESSAGE_DEFAULT_LIMIT = 50
MESSAGE_MAX_LIMIT = 100

# 종료 의도. 분명한 말만 clear, 지친 기색은 unsure(확인 질문).
_END_CLEAR = re.compile(
    r"그만\s*(하자|할래|할게|하고\s*싶|해도|해)|^\s*그만\s*[.!~]*\s*$|끝낼래|끝낼게|끝내자|끝내고\s*싶"
    r"|이제\s*끝|^\s*끝\s*[.!~]*\s*$|안\s*할래|그만$"
)
_END_UNSURE = re.compile(r"졸려|피곤해|힘들어|지겨워|지루해|나중에\s*(할래|하자)|이제\s*됐|다\s*했어")
END_OPTIONS = [{"id": "END", "label": "응, 그만할래"}, {"id": "CONTINUE", "label": "아니, 더 할래"}]


def new_question_id() -> str:
    return f"q_{uuid.uuid4().hex}"


def end_intent(text: str) -> str:
    """clear|unsure|none — 규칙 판정."""
    t = " ".join((text or "").split())
    if _END_CLEAR.search(t):
        return "clear"
    if _END_UNSURE.search(t):
        return "unsure"
    return "none"


def combine_end_intent(rule: str, ai: str | None) -> str:
    if "clear" in (rule, ai):
        return "clear"
    if "unsure" in (rule, ai):
        return "unsure"
    return "none"


# --- 세션 · 메시지 --------------------------------------------------------------


def own_session(db: Session, cu: CurrentUser, session_id: str, kind: str) -> ConversationSession:
    session = db.get(ConversationSession, session_id)
    if session is None or session.user_id != cu.id or session.kind != kind:
        raise ApiError(404, "SESSION_NOT_FOUND", "대화를 찾을 수 없어요.")
    if not conversation_scope.belongs(db, session.id, cu.child.id):
        raise ApiError(404, "SESSION_NOT_FOUND", "대화를 찾을 수 없어요.")
    return session


def ensure_open(session: ConversationSession) -> None:
    if session.status not in OPEN_STATUSES:
        raise ApiError(409, "SESSION_CLOSED", "이미 끝났거나 멈춘 대화예요.", {"status": session.status})


def interaction(move: str, prompt: str, options: list[dict] | None = None, **extra) -> dict:
    return {
        "type": "SINGLE_CHOICE" if options else "TEXT",
        "questionId": new_question_id(),
        "options": list(options or []),
        "move": move,
        "prompt": prompt,
        **extra,
    }


def interaction_out(data: dict | None) -> Interaction | None:
    if not data:
        return None
    return Interaction(
        type=data["type"],
        question_id=data["questionId"],
        options=[ChoiceOption(id=o["id"], label=o["label"]) for o in data.get("options", [])],
    )


def add_message(
    db: Session,
    session: ConversationSession,
    *,
    role: str,
    content: str,
    source: str,
    question_id: str | None = None,
    answer: dict | None = None,
    client_message_id: str | None = None,
    meta: dict | None = None,
) -> ConversationMessage:
    seq = (
        db.scalar(select(func.max(ConversationMessage.seq)).where(ConversationMessage.session_id == session.id)) or 0
    ) + 1
    message = ConversationMessage(
        session_id=session.id,
        seq=seq,
        role=role,
        content=content,
        source=source,
        question_id=question_id,
        answer=answer,
        client_message_id=client_message_id,
        meta=meta or {},
        created_at=clock.now(),
    )
    db.add(message)
    db.flush()
    return message


def add_assistant(db: Session, session: ConversationSession, content: str, source: str, data: dict | None):
    snapshot = {"type": data["type"], "questionId": data["questionId"], "options": data["options"]} if data else None
    return add_message(
        db,
        session,
        role="ASSISTANT",
        content=content,
        source=source,
        question_id=data["questionId"] if data else None,
        answer=snapshot,
    )


def message_out(message: ConversationMessage) -> MessageOut:
    answer = message.answer or {}
    if message.role == "USER":
        return MessageOut(
            id=message.id,
            role="USER",
            content=message.content,
            question_id=message.question_id,
            answer=AnswerOut(type=answer.get("type", "TEXT"), option_id=answer.get("optionId")),
            created_at=iso(message.created_at) or "",
        )
    return MessageOut(
        id=message.id,
        role="ASSISTANT",
        content=message.content,
        question_id=message.question_id,
        interaction=interaction_out(answer) if answer else None,
        source="ai" if message.source == "ai" else "fallback",
        created_at=iso(message.created_at) or "",
    )


def page_messages(
    db: Session, session: ConversationSession, message_cursor: str | None, limit: int | None
) -> tuple[list[MessageOut], str | None]:
    """최신 limit 개를 오름차순으로. 커서(메시지 id)가 있으면 그보다 오래된 것들."""
    if limit is None:
        limit = MESSAGE_DEFAULT_LIMIT
    if limit < 1 or limit > MESSAGE_MAX_LIMIT:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["limit"]})
    query = select(ConversationMessage).where(ConversationMessage.session_id == session.id)
    if message_cursor:
        anchor = db.get(ConversationMessage, message_cursor)
        if anchor is None or anchor.session_id != session.id:
            raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["messageCursor"]})
        query = query.where(ConversationMessage.seq < anchor.seq)
    rows = list(db.scalars(query.order_by(ConversationMessage.seq.desc()).limit(limit + 1)))
    more = len(rows) > limit
    rows = list(reversed(rows[:limit]))
    return [message_out(m) for m in rows], (rows[0].id if more and rows else None)


def find_by_client_id(db: Session, session: ConversationSession, client_message_id: str) -> ConversationMessage | None:
    return db.scalar(
        select(ConversationMessage).where(
            ConversationMessage.session_id == session.id, ConversationMessage.client_message_id == client_message_id
        )
    )


def message_route(session: ConversationSession) -> str:
    return f"POST messages {session.id}"


# --- 입력 해석 · 안전 ------------------------------------------------------------


def resolve_input(session: ConversationSession, req: MessageRequest) -> tuple[dict | None, str | None, str]:
    """현재 질문과 맞는지 확인하고 (지금 질문, 고른 선택지 id, 원문)을 돌려준다."""
    current = session.current_interaction
    if req.question_id and (current is None or req.question_id != current.get("questionId")):
        raise ApiError(409, "QUESTION_MISMATCH", "지난 질문에 답했어요. 화면을 새로 고쳐 주세요.")
    if req.input.type == "SINGLE_CHOICE":
        options = (current or {}).get("options") or []
        option = next((o for o in options if o["id"] == req.input.option_id), None)
        if current is None or current.get("type") != "SINGLE_CHOICE" or option is None:
            raise ApiError(409, "QUESTION_MISMATCH", "지금 질문에 없는 선택지예요.")
        return current, option["id"], option["label"]
    return current, None, " ".join((req.input.text or "").split())


def screen(
    db: Session,
    cu: CurrentUser,
    text: str,
    *,
    allow_personal_info: bool,
    names: bool,
    preserve_school_types: bool = False,
) -> tuple[str, list[str]]:
    """민감 주제·금칙어(→ 422, 원문 저장 안 함) → 개인정보 가리기 → (AI 허용 시) Moderation.

    돌려주는 값: (가린 문장, 가린 항목 종류)
    """
    flag = sensitive.detect(text)
    if flag and not (allow_personal_info and flag.category == "personal_info"):
        _unsafe(db, cu, flag.category, flag.escalate, flag.redirect)
    masked = pii.mask(text, names=names, preserve_school_types=preserve_school_types)
    result = ai_gate.moderate(cu.child, masked.text)
    if result is not None and result.flagged:
        self_harm = any(c.startswith("self-harm") for c in result.categories)
        category = "self_harm" if self_harm else "moderation"
        _unsafe(db, cu, category, self_harm, sensitive.REDIRECTS[category])
    return masked.text, masked.categories


def _unsafe(db: Session, cu: CurrentUser, category: str, escalate: bool, line: str) -> None:
    # 종류만 남긴다. 자해 신호는 보호자에게 알릴 사건(escalate)이다.
    db.add(SafetyEvent(child_id=cu.child.id, talk_id=None, category=category, escalate=escalate))
    db.commit()
    raise ApiError(422, "UNSAFE_CONTENT", line)


def safe_line(text: str, limit: int) -> str | None:
    """AI 가 만든 한 줄 검사: 길이 자르기 + 민감 주제·개인정보 요청이 없어야 한다."""
    line = clip(text, limit)
    if not line or sensitive.detect(line):
        return None
    return line
