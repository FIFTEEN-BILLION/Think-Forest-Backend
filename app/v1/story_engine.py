"""티키와 이야기 — 주제로 대화하며 학습 차원을 모으고, 끝낼 때 정리본을 책장에 저장한다.

한 번의 답은 이 순서를 지난다.
  질문 확인(QUESTION_MISMATCH) → 민감 주제·금칙어(422) → 개인정보 가림 → (허용 시) Moderation
  → 문장 확인 → 규칙 신호로 차원 판정 → 다음 질문 종류(결정론) → (허용 시) AI 문장·신호 → 준비 판정
학습 차원: EXPERIENCE, IDEA, REASON, ALTERNATIVE, REFLECTION.
READY_TO_FINISH = 모든 차원 + 유효 응답 수 ≥ CONVERSATION_MIN_RESPONSES + 실제 대화 시간 ≥ CONVERSATION_MIN_SECONDS.
어떤 질문을 던질지·언제 끝낼 수 있는지는 AI 가 아니라 여기서 정한다. AI 신호는 차원을 보탤 수만 있다.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from ..prompts import talk as talk_prompt
from ..prompts import v1_conversation as prompt
from ..schemas.talk import PlotLLM
from ..talks import plot as plots
from ..talks.planner import active_delta, clip
from ..talks.sentences import CHANGED, EXPAND_LINES, KEPT, check_sentence, has_reason
from . import ai_gate, chat, idempotency, topic_catalog
from .cursor import iso
from .deps import CurrentUser
from .errors import ApiError
from .korean import call_name, has_batchim, vocative
from .models import prefixed_id
from .models_conversation import ChildProfile, ConversationMessage, ConversationSession, StoryRecord
from .schemas_conversation import (
    ConversationCompletion,
    ConversationDetail,
    ConversationMessageResponse,
    ConversationStartResponse,
    ConversationSummary,
    MessageRequest,
    StoryOut,
    StoryReadiness,
    StoryTurnLLM,
    ThoughtJourney,
    TopicRef,
)

KIND = "STORY"
DIMENSIONS: tuple[str, ...] = ("EXPERIENCE", "IDEA", "REASON", "ALTERNATIVE", "REFLECTION")
PLAN: tuple[tuple[str, str], ...] = (
    ("EXPERIENCE", "experience"),
    ("IDEA", "idea"),
    ("REASON", "reason"),
    ("ALTERNATIVE", "alternative"),
    ("REFLECTION", "reflect_choice"),
)
EXTRA_MOVES: tuple[str, ...] = ("imagine", "connect", "tail")
# 이 질문에 제대로 답하면 채워지는 차원
MOVE_DIMENSION = {
    "experience": "EXPERIENCE",
    "idea": "IDEA",
    "reason": "REASON",
    "alternative": "ALTERNATIVE",
    "reflect_why": "REFLECTION",
}
FIXED_CHOICE_MOVES = ("reflect_choice", "confirm_end")
NO_OPTION_MOVES = ("reason", "reflect_why", "tail")
REFLECT_OPTIONS = [{"id": "KEPT", "label": "처음 생각 그대로야"}, {"id": "CHANGED", "label": "생각이 조금 바뀌었어"}]
# 기존 이야기 플롯(talks/plot.py) 장면 제목을 쓰려고 v1 질문 종류를 기존 종류로 옮긴다.
PLOT_MOVE = {
    "experience": "connect",
    "idea": "hook",
    "reason": "tail",
    "alternative": "challenge",
    "reflect_choice": "reason_check",
    "reflect_why": "reason_check",
    "imagine": "imagine",
    "connect": "connect",
    "tail": "tail",
    "continue": "continue",
}
HISTORY = 8
_EXPERIENCE = re.compile(r"본\s*적|봤|보았|해\s*봤|했었|가\s*봤|먹어\s*봤|전에|어제|지난|우리\s*집|학교에서|놀이터")
_IDEA = re.compile(r"것\s*같|거\s*같|생각해|생각했|일\s*거|아닐까|인\s*것|같아")
_ALTERNATIVE = re.compile(r"만약|아니면|다른|아닐\s*수도|반대로|하지만|그런데|없으면|않으면|안\s*생길")
_REFLECTION = re.compile(r"처음에|알게\s*됐|배웠|이제는|지금은")
_YES_OPTION = re.compile(r"^(응|어|네|예|그래|있어|봤어)")
_NO_OPTION = re.compile(r"^(아니|없어|안\s*봤|못\s*봤)")


# --- 준비 판정 ------------------------------------------------------------------


def _state(session: ConversationSession) -> dict:
    stored = session.readiness or {}
    return {
        "covered": list(stored.get("covered", [])),
        "validResponses": int(stored.get("validResponses", 0)),
        "extraTurns": int(stored.get("extraTurns", 0)),
        "readyAnnounced": bool(stored.get("readyAnnounced", False)),
    }


def _is_ready(state: dict, active_seconds: int) -> bool:
    settings = get_settings()
    return (
        set(DIMENSIONS) <= set(state["covered"])
        and state["validResponses"] >= settings.conversation_min_responses
        and active_seconds >= settings.conversation_min_seconds
    )


def readiness_out(session: ConversationSession) -> StoryReadiness:
    settings = get_settings()
    state = _state(session)
    covered = [d for d in DIMENSIONS if d in state["covered"]]
    missing = [d for d in DIMENSIONS if d not in state["covered"]]
    ready = _is_ready(state, session.active_seconds)
    if ready:
        progress = 100
    else:
        responses = min(state["validResponses"] / max(settings.conversation_min_responses, 1), 1)
        seconds = min(session.active_seconds / max(settings.conversation_min_seconds, 1), 1)
        progress = min(99, int(len(covered) * 16 + responses * 10 + seconds * 10))
    return StoryReadiness(ready=ready, progress=progress, covered_dimensions=covered, missing_dimensions=missing)


def plan_move(covered: set[str], pending_move: str | None, choice_id: str | None, ready: bool, extra: int) -> str:
    if pending_move == "reflect_choice" and choice_id:
        return "reflect_why"
    for dimension, move in PLAN:
        if dimension not in covered:
            return move
    return "continue" if ready else EXTRA_MOVES[extra % len(EXTRA_MOVES)]


def rule_dimensions(text: str, pending_move: str | None, covered: set[str]) -> set[str]:
    """문장으로 인정된 답에서 규칙으로 읽은 차원."""
    found = {MOVE_DIMENSION[pending_move]} if pending_move in MOVE_DIMENSION else set()
    if pending_move == "reflect_choice":
        found.add("REFLECTION")
    if _EXPERIENCE.search(text):
        found.add("EXPERIENCE")
    if _IDEA.search(text):
        found.add("IDEA")
    if has_reason(text):
        found |= {"IDEA", "REASON"}
    if "IDEA" in covered and _ALTERNATIVE.search(text):
        found.add("ALTERNATIVE")
    if (
        {"IDEA"} <= covered
        and ({"REASON", "ALTERNATIVE"} & covered)
        and (_REFLECTION.search(text) or CHANGED.search(text) or KEPT.search(text))
    ):
        found.add("REFLECTION")
    return found


def ai_dimensions(out: StoryTurnLLM, covered: set[str]) -> set[str]:
    found: set[str] = set()
    if out.shared_experience:
        found.add("EXPERIENCE")
    if out.child_idea.strip():
        found.add("IDEA")
    if out.reason_given:
        found.add("REASON")
    if out.new_idea and "IDEA" in covered:
        found.add("ALTERNATIVE")
    if out.stance != "none" and "IDEA" in covered:
        found.add("REFLECTION")
    return found


# --- 티키 대사 ------------------------------------------------------------------


def question_for(move: str, topic: dict, meaning: str | None = None) -> tuple[str, list[dict]]:
    if move == "experience":
        return topic.get("opener") or topic_catalog.DEFAULT_OPENER, topic_catalog.OPENER_OPTIONS
    if move == "idea":
        return _idea_question(topic.get("hook", "")), []
    if move == "reason":
        return "왜 그렇게 생각했어? ‘왜냐하면’으로 말해 줄래?", []
    if move == "alternative":
        return topic.get("challenge") or "정말 언제나 그럴까? 아닐 수도 있는 경우를 떠올려 볼래?", []
    if move == "reflect_choice":
        return "이야기해 보니 처음 생각은 어때?", REFLECT_OPTIONS
    if move == "reflect_why":
        if meaning == "KEPT":
            return "처음 생각을 지킨 까닭을 한 문장으로 말해 줄래?", []
        return "생각이 어떻게 바뀌었는지 한 문장으로 말해 줄래?", []
    if move == "imagine":
        return topic.get("imagine") or "그 장면 속에 네가 들어갔다고 상상해 보자. 무엇이 보여?", []
    if move == "connect":
        return topic.get("connect") or "이 생각을 우리 생활 어디에서 볼 수 있을까?", []
    if move == "tail":
        return "그 생각을 조금 더 자세히 들려줄래?", []
    return "더 궁금한 게 있거나 상상해 보고 싶은 게 있어?", []


def _idea_question(hook: str) -> str:
    """경험은 첫 질문에서 이미 물었으니 '본 적 있니? …' 로 시작하는 주제 질문은 뒤 질문만 쓴다."""
    parts = [p.strip() for p in re.split(r"(?<=\?)\s+", hook) if p.strip()]
    if len(parts) > 1 and "본 적" in parts[0]:
        return " ".join(parts[1:])
    return hook


def _echo(text: str, verb: str) -> str:
    said = " ".join(text.split()).rstrip(".!?~ ")
    if len(said) > 30:
        return "네 생각을 들려줘서 고마워!" if verb == "생각했구나" else "자세히 말해 줘서 고마워!"
    return f"“{said}”{'이라고' if has_batchim(said) else '라고'} {verb}."


def reaction_for(move: str | None, text: str, choice_id: str | None) -> str:
    if choice_id in ("SEEN", "YES"):
        return "본 적 있구나!"
    if choice_id == "NOT_SEEN":
        return "처음이구나! 괜찮아, 같이 상상해 보자."
    if choice_id == "KEPT":
        return "처음 생각을 지키기로 했구나."
    if choice_id == "CHANGED":
        return "생각이 자랐구나!"
    if choice_id:
        return f"‘{clip(text, 20)}’를 골랐구나."
    if move == "experience":
        return "그런 일이 있었구나!"
    if move == "reason":
        return "이유까지 말해 줘서 네 생각이 잘 보여."
    if move == "alternative":
        return "다른 경우까지 떠올렸네!"
    if move == "reflect_why":
        return "처음 생각이랑 지금 생각을 견주어 봤구나."
    return _echo(text, "생각했구나" if move == "idea" else "말했구나")


def _validated_options(out: StoryTurnLLM) -> list[dict]:
    options: list[dict] = []
    for index, option in enumerate(out.options[:4]):
        label = chat.safe_line(option.label, 20)
        if not label or any(o["label"] == label for o in options):
            continue
        code = re.sub(r"[^A-Z0-9_]", "", re.sub(r"[\s\-]+", "_", option.id.strip().upper()))[:24]
        if not code or any(o["id"] == code for o in options):
            code = f"OPTION_{index + 1}"
        options.append({"id": code, "label": label})
    return options if 2 <= len(options) <= 4 else []


def _match_option(text: str, options: list[dict]) -> dict | None:
    """객관식 질문에 짧게 글로 답한 경우 선택지로 알아듣는다."""
    t = text.strip()
    for option in options:
        if t and (t in option["label"] or option["label"] in t):
            return option
    if len(options) >= 2 and _NO_OPTION.match(t):
        return options[1]
    if options and _YES_OPTION.match(t):
        return options[0]
    return None


# --- 직렬화 ---------------------------------------------------------------------


def topic_ref(session: ConversationSession) -> TopicRef:
    topic = session.topic or {}
    return TopicRef(
        id=topic.get("apiId"), title=topic.get("title", ""), category=topic.get("apiCategory", "IMAGINATION")
    )


def detail_out(
    db: Session, session: ConversationSession, message_cursor: str | None = None, limit: int | None = None
) -> ConversationDetail:
    messages, next_cursor = chat.page_messages(db, session, message_cursor, limit)
    return ConversationDetail(
        conversation_id=session.id,
        status=session.status,
        topic=topic_ref(session),
        messages=messages,
        next_cursor=next_cursor,
        current_interaction=chat.interaction_out(session.current_interaction),
        readiness=readiness_out(session),
        story_id=session.story_id,
        created_at=iso(session.created_at) or "",
        updated_at=iso(session.updated_at) or "",
    )


def summary_out(session: ConversationSession) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=session.id,
        status=session.status,
        topic=topic_ref(session),
        readiness=readiness_out(session),
        story_id=session.story_id,
        created_at=iso(session.created_at) or "",
        updated_at=iso(session.updated_at) or "",
    )


def story_out(story: StoryRecord) -> StoryOut:
    journey = story.thought_journey or {}
    return StoryOut(
        id=story.id,
        title=story.title,
        summary=story.summary,
        body=story.body,
        thought_journey=ThoughtJourney(
            initial_idea=journey.get("initialIdea", ""),
            evidence=list(journey.get("evidence", [])),
            alternatives=list(journey.get("alternatives", [])),
            final_reflection=journey.get("finalReflection", ""),
        ),
        category=story.category,
        topic=TopicRef(id=story.topic_id, title=story.topic_title, category=story.category),
        favorite=story.favorite,
        version=story.version,
        source_conversation_id=story.session_id,
        created_at=iso(story.created_at) or "",
        updated_at=iso(story.updated_at) or "",
    )


# --- 대화 시작 · 한 번 ----------------------------------------------------------


def _nickname(db: Session, cu: CurrentUser) -> str | None:
    profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == cu.child.id))
    return (profile.nickname if profile else None) or cu.child.nickname or None


def start(db: Session, cu: CurrentUser, topic_id: str) -> ConversationStartResponse:
    topic = topic_catalog.resolve(db, cu.id, topic_id)
    if topic is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "주제를 찾을 수 없어요.")
    now = clock.now()
    session = ConversationSession(
        id=prefixed_id("cnv"),
        user_id=cu.id,
        kind=KIND,
        topic=topic,
        status="ACTIVE",
        readiness=_state_blank(),
        last_message_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    nickname = _nickname(db, cu)
    question, options = question_for("experience", topic)
    hello = f"안녕, {vocative(nickname)}!" if nickname else "안녕!"
    content = f"{hello} 오늘은 ‘{topic['title']}’ 이야기를 해 보자. {question}"
    data = chat.interaction("experience", question, options)
    assistant = chat.add_assistant(db, session, content, "fallback", data)
    session.current_interaction = data
    return ConversationStartResponse(
        conversation_id=session.id,
        status=session.status,
        topic=topic_ref(session),
        assistant_message=chat.message_out(assistant),
        next_interaction=chat.interaction_out(data),
        readiness=readiness_out(session),
    )


def _state_blank() -> dict:
    return {"covered": [], "validResponses": 0, "extraTurns": 0, "readyAnnounced": False}


def handle_message(db: Session, cu: CurrentUser, session: ConversationSession, req: MessageRequest):
    current, option_id, raw = chat.resolve_input(session, req)
    current = current or {}
    pending = current.get("move")
    options = current.get("options") or []
    if option_id:
        masked = raw
    else:
        masked = chat.screen(db, cu, raw, allow_personal_info=False, names=True)[0]
        if options and (matched := _match_option(raw, options)) and not check_sentence(raw).ok:
            option_id = matched["id"]  # "응 봤어"처럼 짧게 글로 고른 경우

    now = clock.now()
    session.active_seconds += active_delta(session.last_message_at, now)
    session.last_message_at = now
    state = _state(session)
    covered_before = set(state["covered"])
    topic = session.topic or {}

    end = chat.end_intent(raw) if req.input.type == "TEXT" else "none"
    continued = False
    if pending == "confirm_end" and option_id:
        end, continued = ("clear" if option_id == "END" else "none"), option_id == "CONTINUE"
        valid, found = False, set()
    elif option_id:
        valid = True
        found = {MOVE_DIMENSION[pending]} if pending in ("experience", "idea", "alternative") else set()
    else:
        check = check_sentence(raw)
        valid = check.ok and end == "none"
        found = rule_dimensions(raw, pending, covered_before) if valid else set()

    user_message = chat.add_message(
        db,
        session,
        role="USER",
        content=masked,
        source="child",
        question_id=current.get("questionId"),
        answer={"type": "SINGLE_CHOICE" if option_id else "TEXT", "optionId": option_id, "options": options},
        client_message_id=req.client_message_id,
        meta={"move": pending, "valid": valid, "choice": option_id},
    )
    covered = covered_before | found
    extra = state["extraTurns"]
    meaning = option_id if pending == "reflect_choice" else current.get("meaning")

    # 다음 질문 종류(결정론). 문장이 아니면 같은 질문을 다시 묻는다. 끝낼지 확인 중이었으면 그 전 질문으로 돌아간다.
    retry = not valid and not option_id and end == "none"
    move = (current.get("resumeMove") if pending == "confirm_end" else pending) or "idea"
    if not retry and not continued and end == "none":
        state["validResponses"] += 1
        move = plan_move(
            covered, pending, option_id, _is_ready({**state, "covered": list(covered)}, session.active_seconds), extra
        )

    source, reaction, ai_question, ai_options = "fallback", None, None, []
    if valid and ai_gate.budget(cu.child, session) is None:
        history = [
            (m.role, m.content)
            for m in reversed(
                list(
                    db.scalars(
                        select(ConversationMessage)
                        .where(ConversationMessage.session_id == session.id, ConversationMessage.id != user_message.id)
                        .order_by(ConversationMessage.seq.desc())
                        .limit(HISTORY)
                    )
                )
            )
        ]
        try:
            out = ai_gate.call(
                purpose="conversation.turn",
                instructions=prompt.story_turn_instructions(),
                user_input=prompt.story_turn_input(_nickname(db, cu), topic, history, move, masked),
                schema=StoryTurnLLM,
            )
            source = "ai"
            end = chat.combine_end_intent(end, out.end_intent) if not option_id else end
            extra_found = ai_dimensions(out, covered) if not option_id else set()
            if extra_found - covered:
                covered |= extra_found
                replanned = plan_move(
                    covered,
                    pending,
                    option_id,
                    _is_ready({**state, "covered": list(covered)}, session.active_seconds),
                    extra,
                )
                if replanned != move:
                    move = replanned  # AI 신호로 차원이 채워져 질문 종류가 바뀌면 AI 질문 대신 규칙 질문을 쓴다
                    out.question = ""
            reaction = chat.safe_line(out.reaction, 60)
            question = chat.safe_line(out.question, 90)
            if question and question.endswith("?") and move not in FIXED_CHOICE_MOVES and move != "experience":
                ai_question = question
                ai_options = [] if move in NO_OPTION_MOVES else _validated_options(out)
        except ai_gate.LlmError:
            source = "fallback"
    user_message.meta = {**user_message.meta, "dims": sorted(covered - covered_before), "source": source}

    state["covered"] = [d for d in DIMENSIONS if d in covered]
    if valid and move in EXTRA_MOVES:
        state["extraTurns"] = extra + 1
    session.readiness = state
    ready = _is_ready(state, session.active_seconds)

    next_data = None
    if end == "clear" and ready:
        content = f"좋아, {vocative(_nickname(db, cu))}! 오늘 나눈 생각으로 이야기를 정리할게."
    elif end == "clear":
        question, opts = question_for(move, topic, meaning)
        content = f"조금만 더 이야기하면 멋진 이야기가 완성돼! {question}"
        next_data = chat.interaction(move, question, opts)
    elif end == "unsure":
        content = "조금 쉬고 싶어? 오늘은 여기까지 할지 골라 줘."
        next_data = chat.interaction("confirm_end", content, chat.END_OPTIONS, resumeMove=move)
    elif retry:
        content = EXPAND_LINES[check.reason or "too_short"]
        next_data = {**current, "questionId": current.get("questionId") or chat.new_question_id()}
    else:
        if ai_question:
            question, opts = ai_question, ai_options
        else:
            question, opts = question_for(move, topic, meaning)
        if continued:
            reaction = "좋아, 계속 이야기하자!"
        elif reaction is None:
            reaction = reaction_for(pending, raw, option_id)
        prefix = ""
        if ready and not state["readyAnnounced"]:
            prefix = "이제 이야기를 정리할 수 있어! 다 됐으면 마치기 버튼을 눌러 줘. 더 이야기해도 좋아. "
            state["readyAnnounced"] = True
            session.readiness = dict(state)
        content = f"{reaction} {prefix}{question}".strip()
        next_data = chat.interaction(move, question, opts, meaning=meaning)

    assistant = chat.add_assistant(db, session, content, source, next_data)
    session.current_interaction = next_data
    session.status = "READY_TO_FINISH" if ready else "ACTIVE"
    session.updated_at = now
    completion = complete(db, cu, session) if end == "clear" and ready else None
    response = ConversationMessageResponse(
        user_message=chat.message_out(user_message),
        assistant_message=chat.message_out(assistant),
        next_interaction=chat.interaction_out(next_data),
        readiness=readiness_out(session),
        status=session.status,
        end_intent_detected=end == "clear",
        completion=completion,
    )
    idempotency.remember(db, cu.id, req.client_message_id, chat.message_route(session), response)
    db.commit()
    return response


# --- 완료 · 취소 ----------------------------------------------------------------


def _quote(text: str, limit: int = 80) -> str:
    return clip(text, limit).rstrip(".!?~ ")


def thought_journey(messages: list[ConversationMessage]) -> dict:
    """아이 원문 인용으로 생각 과정을 정리한다. 고른 선택지는 처음 생각으로만 쓰고 근거로 쓰지 않는다."""
    valid = [m for m in messages if m.role == "USER" and (m.meta or {}).get("valid")]
    said = [m for m in valid if not (m.meta or {}).get("choice")]

    def with_dim(rows: list[ConversationMessage], dim: str) -> list[ConversationMessage]:
        return [m for m in rows if dim in (m.meta or {}).get("dims", [])]

    ideas = with_dim(valid, "IDEA") or said
    reflections = with_dim(said, "REFLECTION")
    evidence = [m for m in said if {"REASON", "EXPERIENCE"} & set((m.meta or {}).get("dims", []))]
    return {
        "initialIdea": _quote(ideas[0].content) if ideas else "",
        "evidence": [_quote(m.content) for m in evidence[:3]],
        "alternatives": [_quote(m.content) for m in with_dim(said, "ALTERNATIVE")[:3]],
        "finalReflection": _quote((reflections or said)[-1].content) if said else "",
    }


def _said(quote: str) -> str:
    return f"“{quote}”{'이라고' if has_batchim(quote) else '라고'}"


def _body(topic_title: str, journey: dict) -> str:
    lines = [f"‘{topic_title}’ 이야기를 나눴어요."]
    if journey["initialIdea"]:
        lines.append(f"처음에는 {_said(journey['initialIdea'])} 생각했어요.")
    if journey["evidence"]:
        lines.append(f"그렇게 생각한 단서로 {_said(journey['evidence'][0])} 말했어요.")
    if journey["alternatives"]:
        lines.append(f"다른 경우도 떠올려 봤어요. “{journey['alternatives'][0]}”")
    if journey["finalReflection"]:
        lines.append(f"마지막에는 {_said(journey['finalReflection'])} 정리했어요.")
    return " ".join(lines)


def _ai_plot(cu: CurrentUser, session: ConversationSession, nickname: str | None, said: list[dict]) -> dict | None:
    if ai_gate.budget(cu.child, session) is not None:
        return None
    try:
        out = ai_gate.call(
            purpose="conversation.plot",
            instructions=talk_prompt.plot_instructions("topic"),
            user_input=talk_prompt.plot_input(nickname or "", session.topic or {}, said),
            schema=PlotLLM,
            max_output_tokens=3000,
        )
    except ai_gate.LlmError:
        return None
    return plots.validate_plot(
        [s.model_dump() for s in out.scenes],
        out.title,
        out.ending_question,
        {t["id"] for t in said},
        session.topic or {},
    )


def complete(db: Session, cu: CurrentUser, session: ConversationSession) -> ConversationCompletion:
    """완료. commit 은 호출자가 한다. 이미 완료된 대화는 처음 결과를 그대로 돌려준다."""
    if session.status == "COMPLETED" and session.result:
        return ConversationCompletion.model_validate(session.result)
    chat.ensure_open(session)
    if not _is_ready(_state(session), session.active_seconds):
        settings = get_settings()
        readiness = readiness_out(session)
        state = _state(session)
        raise ApiError(
            409,
            "CONVERSATION_NOT_READY",
            "조금 더 이야기한 뒤 마칠 수 있어요.",
            {
                "missingDimensions": readiness.missing_dimensions,
                "remainingResponses": max(settings.conversation_min_responses - state["validResponses"], 0),
                "remainingSeconds": max(settings.conversation_min_seconds - session.active_seconds, 0),
            },
        )
    session.status = "FINALIZING"
    db.flush()

    topic = session.topic or {}
    messages = list(
        db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.session_id == session.id)
            .order_by(ConversationMessage.seq)
        )
    )
    journey = thought_journey(messages)
    nickname = _nickname(db, cu)
    said = [
        {"id": m.id, "move": PLOT_MOVE.get((m.meta or {}).get("move") or "", "tail"), "text": m.content, "seq": m.seq}
        for m in messages
        if m.role == "USER" and (m.meta or {}).get("valid") and not (m.meta or {}).get("choice")
    ]
    plot = _ai_plot(cu, session, nickname, said)
    title = plot["title"] if plot else f"{nickname or '나'}의 ‘{topic.get('title', '').rstrip('?')}’ 이야기"
    who = call_name(nickname) if nickname else "아이"
    summary = (
        f"{who}는 ‘{topic.get('title', '')}’ 이야기를 하며 처음 생각과 그 이유를 말하고, "
        "다른 경우를 떠올려 본 뒤 자기 생각을 정리했어요."
    )
    now = clock.now()
    story = StoryRecord(
        user_id=cu.id,
        session_id=session.id,
        category=topic.get("apiCategory", "IMAGINATION"),
        topic_id=topic.get("apiId"),
        topic_title=clip(topic.get("title", ""), 80),
        title=clip(title, 80),
        summary=summary,
        body=_body(topic.get("title", ""), journey),
        thought_journey=journey,
        ai_original=plot,
        source="ai" if plot else "fallback",
        favorite=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(story)
    db.flush()
    completion = ConversationCompletion(status="COMPLETED", story=story_out(story))
    session.status = "COMPLETED"
    session.story_id = story.id
    session.completed_at = now
    session.updated_at = now
    session.current_interaction = None
    session.result = completion.model_dump(mode="json", by_alias=True)
    return completion


def cancel(session: ConversationSession) -> None:
    if session.status == "CANCELLED":
        return
    if session.status == "COMPLETED":
        raise ApiError(409, "SESSION_CLOSED", "이미 끝난 대화예요.", {"status": session.status})
    now = clock.now()
    session.status = "CANCELLED"
    session.cancelled_at = now
    session.updated_at = now
    session.current_interaction = None
