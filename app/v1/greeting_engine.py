"""AI가 첫인사의 대화·추출·정정·질문을 담당한다. 서버는 출처 검증과 상태 저장만 담당한다."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import clock
from ..prompts import v1_conversation as prompt
from ..talks.planner import active_delta
from . import ai_gate, chat, conversation_scope, idempotency
from .cursor import iso
from .deps import CurrentUser
from .errors import ApiError
from .greeting_profile import InvalidGreetingOutput, apply_changes
from .models import prefixed_id
from .models_conversation import ChildProfile, ConversationFact, ConversationMessage, ConversationSession
from .schemas_conversation import (
    FirstGreetingLLM,
    GreetingCompletion,
    GreetingMessageResponse,
    GreetingReadiness,
    GreetingSessionOut,
    MessageRequest,
    ProfileDraftOut,
    ProfileOut,
)

KIND = "FIRST_GREETING"
FIELDS = {
    "NICKNAME": "nickname",
    "GRADE_OR_AGE": "gradeOrAgeBand",
    "INTEREST": "interests",
    "INTEREST_DETAIL": "interestDetails",
    "GROWTH_GOAL": "growthGoal",
}
AFFILIATION_OF = {"초등학교": "elementary", "홈스쿨": "homeschool", "유치원": "other", "기타": "other"}
# AI가 정규화한 저장값을 기존 children.grade 칼럼에 옮기는 직렬화 매핑. 발화 분석이 아니다.
LEGACY_GRADES = {label: n for n in range(1, 7) for label in (f"{n}학년", f"초등학교 {n}학년")}
PROFILE_OPTIONS = [
    {"id": "CONFIRM_PROFILE", "label": "맞아요, 첫 인사 마치기"},
    {"id": "EDIT_NICKNAME", "label": "이름·별명 고칠래"},
    {"id": "EDIT_SCHOOL", "label": "소속 고칠래"},
    {"id": "EDIT_INTEREST", "label": "좋아하는 것 고칠래"},
]
HISTORY_LIMIT = 40


def empty_draft() -> dict:
    return {
        "nickname": None,
        "schoolOrGroup": None,
        "gradeOrAgeBand": None,
        "interests": [],
        "interestDetails": [],
        "growthGoal": None,
    }


def draft_of(session: ConversationSession) -> dict:
    stored = (session.readiness or {}).get("draft") or {}
    return {
        k: list(stored[k]) if isinstance(stored.get(k), list) else stored.get(k, v) for k, v in empty_draft().items()
    }


def missing_fields(draft: dict) -> list[str]:
    return [field for field, key in FIELDS.items() if not draft[key]]


def readiness_out(draft: dict) -> GreetingReadiness:
    missing = missing_fields(draft)
    return GreetingReadiness(
        ready=not missing, progress=(len(FIELDS) - len(missing)) * 100 // len(FIELDS), missing=missing
    )


def session_out(db: Session, session: ConversationSession, *, resumed=False, message_cursor=None, limit=None):
    messages, next_cursor = chat.page_messages(db, session, message_cursor, limit)
    state = session.readiness or {}
    return GreetingSessionOut(
        session_id=session.id,
        status=session.status,
        resumed=resumed,
        messages=messages,
        next_cursor=next_cursor,
        current_interaction=chat.interaction_out(session.current_interaction),
        profile_draft=ProfileDraftOut.model_validate(draft_of(session)),
        readiness=readiness_out(draft_of(session)),
        processing=state.get("processing"),
        profile_revision=state.get("revision", 0),
        deferred_fields=state.get("deferred", []),
    )


def _unavailable(reason: str) -> ApiError:
    messages = {
        "guest_consent_required": "티키와 만나기 전에 보호자가 개인정보와 AI 대화에 동의해 주세요.",
        "child_data_mode_off": "AI 대화를 시작하려면 보호자 동의 또는 성인 테스트 계정 설정을 확인해 주세요.",
        "no_api_key": "서버의 AI 연결 설정이 필요해요.",
        "ai_disabled": "지금은 서버의 AI 대화 설정이 꺼져 있어요.",
        "session_call_limit": "이번 대화의 AI 사용 한도에 도달했어요.",
        "daily_limit": "오늘의 AI 사용 한도에 도달했어요.",
    }
    return ApiError(
        503,
        "AI_TEMPORARILY_UNAVAILABLE",
        messages.get(reason, "AI 답변을 받지 못했어요. 입력한 내용은 그대로 두고 다시 시도해 주세요."),
        {"reason": reason, "retryable": reason.startswith("ai_error:") or reason == "invalid_ai_output"},
    )


def _ai_turn(
    db: Session, cu: CurrentUser, session: ConversationSession, current: ConversationMessage | None, *, event: str
) -> tuple[FirstGreetingLLM, dict, list, dict]:
    if reason := ai_gate.budget(cu.child, session):
        raise _unavailable(reason)
    history = list(
        reversed(
            list(
                db.scalars(
                    select(ConversationMessage)
                    .where(ConversationMessage.session_id == session.id)
                    .order_by(ConversationMessage.seq.desc())
                    .limit(HISTORY_LIMIT)
                )
            )
        )
    )
    state = session.readiness or {}
    try:
        out = ai_gate.call(
            purpose="first_greeting.turn",
            instructions=prompt.greeting_instructions(),
            user_input=prompt.greeting_input(
                draft=draft_of(session),
                missing=missing_fields(draft_of(session)),
                deferred=state.get("deferred", []),
                context_summary=state.get("contextSummary", ""),
                history=[{"id": m.id, "role": m.role, "content": m.content} for m in history],
                current_message_id=current.id if current else None,
                event=event,
            ),
            schema=FirstGreetingLLM,
            max_output_tokens=4000,
        )
        # mock과 실제 공급자 모두 동일한 구조 검증을 거친다.
        out = FirstGreetingLLM.model_validate(out)
        draft, deferred, provenance = apply_changes(
            draft_of(session),
            state.get("deferred", []),
            out,
            messages={m.id: m.content for m in history if m.role == "USER"},
            current_id=current.id if current else None,
        )
        if out.propose_review and (missing_fields(draft) or not out.profile_summary):
            raise InvalidGreetingOutput("review_not_ready")
        return out, draft, deferred, provenance
    except ai_gate.LlmError as exc:
        raise _unavailable(f"ai_error:{exc.code}") from None
    except ValueError:
        # 잘못된 출력의 대사와 변경사항을 부분 적용하지 않는다.
        raise _unavailable("invalid_ai_output") from None


def _record_facts(db: Session, session: ConversationSession, before: dict, after: dict, provenance: dict):
    fact_names = {v: k for k, v in FIELDS.items()} | {"schoolOrGroup": "SCHOOL_OR_GROUP"}
    for key, message_id in provenance.items():
        if before[key] == after[key]:
            continue
        field = fact_names[key]
        db.execute(
            update(ConversationFact)
            .where(ConversationFact.session_id == session.id, ConversationFact.field == field)
            .values(current=False)
        )
        if after[key]:
            db.add(
                ConversationFact(
                    session_id=session.id,
                    field=field,
                    value=after[key],
                    source_message_id=message_id,
                    confidence=0.8,
                    current=True,
                    created_at=clock.now(),
                )
            )


def _accept_turn(
    db: Session, session: ConversationSession, out: FirstGreetingLLM, draft: dict, deferred: list, provenance: dict
) -> ConversationMessage:
    before = draft_of(session)
    previous = session.readiness or {}
    revision = previous.get("revision", 0) + int(draft != before)
    _record_facts(db, session, before, draft, provenance)
    review = out.propose_review and not missing_fields(draft)
    interaction = chat.interaction(
        "confirm_profile" if review else "chat",
        out.message,
        PROFILE_OPTIONS if review else None,
        reviewRevision=revision if review else None,
        reviewDraft=draft if review else None,
    )
    session.readiness = {
        "draft": draft,
        "deferred": deferred,
        "contextSummary": out.context_summary,
        "revision": revision,
        "profileSummary": out.profile_summary if review else None,
        "processing": {"mode": "AI", "reason": None},
    }
    session.current_interaction = interaction
    session.status = "READY_TO_FINISH" if review else "ACTIVE"
    assistant = chat.add_assistant(db, session, out.message, "ai", interaction)
    # 원문 인용을 중복 보관하지 않고 출처와 변경 연산만 기록한다.
    assistant.meta = {
        "changes": [
            {"field": c.field, "operation": c.operation, "sourceMessageIds": [e.message_id for e in c.evidence]}
            for c in out.changes
        ],
        "endIntent": out.end_intent,
    }
    return assistant


def start_or_resume(db: Session, cu: CurrentUser) -> GreetingSessionOut:
    existing = db.scalar(
        select(ConversationSession)
        .where(
            ConversationSession.user_id == cu.id,
            conversation_scope.condition(ConversationSession.id, cu.child.id),
            ConversationSession.kind == KIND,
            ConversationSession.status.in_(chat.OPEN_STATUSES),
        )
        .order_by(ConversationSession.created_at.desc())
    )
    if existing:
        return session_out(db, existing, resumed=True)
    now = clock.now()
    session = ConversationSession(
        id=prefixed_id("fgs"),
        user_id=cu.id,
        kind=KIND,
        status="ACTIVE",
        readiness={"draft": empty_draft()},
        last_message_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    conversation_scope.bind(db, session.id, cu.child.id)
    _accept_turn(db, session, *_ai_turn(db, cu, session, None, event="START"))
    return session_out(db, session)


def handle_message(db: Session, cu: CurrentUser, session: ConversationSession, req: MessageRequest):
    current, option_id, raw = chat.resolve_input(session, req)
    if option_id == "CONFIRM_PROFILE" and req.question_id != (current or {}).get("questionId"):
        raise ApiError(409, "QUESTION_MISMATCH", "확인할 정보가 바뀌었어요. 최신 내용을 확인해 주세요.")
    masked = (
        raw
        if option_id
        else chat.screen(
            db,
            cu,
            raw,
            allow_personal_info=True,
            names=False,
            preserve_school_types=True,
        )[0]
    )
    now = clock.now()
    user_message = chat.add_message(
        db,
        session,
        role="USER",
        content=masked,
        source="child",
        question_id=(current or {}).get("questionId"),
        client_message_id=req.client_message_id,
        answer={"type": req.input.type, "optionId": option_id},
    )
    completion = None
    if option_id == "CONFIRM_PROFILE":
        completion = complete(db, cu, session, profile_revision=(current or {}).get("reviewRevision"))
        # 확정 버튼의 처리 결과는 대화 생성이 아니라 시스템 안내다.
        assistant = chat.add_assistant(db, session, "확인한 정보로 첫인사를 마쳤어요.", "fallback", None)
        session.readiness = {**session.readiness, "processing": {"mode": "RULES", "reason": "guided_step"}}
        end_detected = True
    else:
        out, draft, deferred, provenance = _ai_turn(
            db,
            cu,
            session,
            user_message,
            event=option_id or "MESSAGE",
        )
        assistant = _accept_turn(db, session, out, draft, deferred, provenance)
        # 자연어 종료 의도는 AI가 판단하되 프로필 저장 승인을 대신하지 않는다.
        end_detected = out.end_intent == "clear"
    session.active_seconds += active_delta(session.last_message_at, now)
    session.last_message_at = now
    session.updated_at = now
    state = session.readiness
    response = GreetingMessageResponse(
        user_message=chat.message_out(user_message),
        assistant_message=chat.message_out(assistant),
        next_interaction=chat.interaction_out(session.current_interaction),
        profile_draft=ProfileDraftOut.model_validate(draft_of(session)),
        readiness=readiness_out(draft_of(session)),
        status=session.status,
        end_intent_detected=end_detected,
        completion=completion,
        processing=state.get("processing"),
        profile_revision=state.get("revision", 0),
        deferred_fields=state.get("deferred", []),
    )
    idempotency.remember(db, cu.id, req.client_message_id, chat.message_route(session), response)
    db.commit()
    return response


def complete(
    db: Session, cu: CurrentUser, session: ConversationSession, *, profile_revision: int | None
) -> GreetingCompletion:
    """완료. commit 은 호출자가 한다. 이미 완료된 세션은 처음 결과를 그대로 돌려준다."""
    if session.status == "COMPLETED" and session.result:
        return GreetingCompletion.model_validate(session.result)
    chat.ensure_open(session)
    draft = draft_of(session)
    missing = missing_fields(draft)
    if missing:
        raise ApiError(409, "FIRST_GREETING_NOT_READY", "티키가 조금 더 알고 싶은 게 있어요.", {"missing": missing})
    review = session.current_interaction or {}
    state = session.readiness or {}
    if (
        profile_revision is None
        or profile_revision != state.get("revision", 0)
        or review.get("move") != "confirm_profile"
        or review.get("reviewDraft") != draft
        or review.get("reviewRevision") != profile_revision
        or not state.get("profileSummary")
    ):
        raise ApiError(409, "PROFILE_REVIEW_REQUIRED", "최신 프로필 내용을 확인한 뒤 마쳐 주세요.")
    session.status = "FINALIZING"
    db.flush()

    now = clock.now()
    summary = state["profileSummary"]
    profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == cu.child.id))
    if profile is None:
        profile = ChildProfile(child_id=cu.child.id, user_id=cu.id, version=1, created_at=now)
        db.add(profile)
    else:
        profile.version += 1
    profile.nickname = draft["nickname"]
    profile.school_or_group = draft["schoolOrGroup"]
    profile.grade_or_age_band = draft["gradeOrAgeBand"]
    profile.interests = list(draft["interests"])
    profile.interest_details = list(draft["interestDetails"])
    profile.growth_goal = draft["growthGoal"]
    profile.summary = summary
    profile.completed_at = now
    profile.updated_at = now
    db.flush()

    # 기존 엔진으로 넘길 정규화된 학년 값만 매핑한다. 사용자 발화는 분석하지 않는다.
    child = cu.child
    child.nickname = draft["nickname"][:20]
    child.likes = list(draft["interests"][:5])
    child.want_to_learn = [draft["growthGoal"]]
    child.profile_confirmed = True
    if grade := LEGACY_GRADES.get(draft["gradeOrAgeBand"]):
        child.grade = grade
    if draft["schoolOrGroup"] in AFFILIATION_OF:
        child.affiliation = AFFILIATION_OF[draft["schoolOrGroup"]]

    completion = GreetingCompletion(
        status="COMPLETED",
        profile=ProfileOut(
            id=profile.id,
            nickname=profile.nickname,
            school_or_group=profile.school_or_group,
            grade_or_age_band=profile.grade_or_age_band,
            interests=list(profile.interests),
            interest_details=list(profile.interest_details),
            growth_goal=profile.growth_goal,
        ),
        summary=summary,
        completed_at=iso(now) or "",
    )
    session.status = "COMPLETED"
    session.completed_at = now
    session.updated_at = now
    session.current_interaction = None
    session.result = completion.model_dump(mode="json", by_alias=True)
    return completion
