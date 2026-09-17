"""생각 친구 대화 — 주제를 던지고, 꼬리질문으로 이어 가고, 문장으로 정리해 이야기로 완성한다.

한 번의 아이 답은 이 순서를 지난다.
  민감 주제(결정론) → 문장인지 확인 → 개인정보 가림 → (허용 시) Moderation → 흐름 결정(결정론)
  → (허용 시) AI 문장 생성·검증 → 실패하면 규칙 대사
어떤 질문을 던질지, 언제 정리하고 끝낼 수 있는지는 AI 가 아니라 planner 가 정한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import ai_block_reason, guardian_child, require_child, require_guardian
from ..config import get_settings
from ..db import get_session
from ..models import Category, Child, Family, SafetyEvent, SharedItem, Story, Talk, Turn
from ..prompts import talk as prompt
from ..safety import pii
from ..safety import topics as sensitive
from ..schemas.talk import (
    PlotLLM,
    StoryOut,
    StorySceneOut,
    TalkOut,
    TalkStartRequest,
    TalkSummary,
    TalkTurnLLM,
    TimeOut,
    TodayResponse,
    TopicOut,
    TurnOut,
    TurnRequest,
    TurnResponse,
    WordNote,
)
from ..services import moderation, sharing, usage
from ..services.llm import LlmError, call_structured
from ..talks import planner
from ..talks import plot as plots
from ..talks import topics as bank
from ..talks.sentences import EXPAND_LINES, MORE, check_sentence, has_reason, sentence_count, stance_of, starters_for
from .categories import category_list

router = APIRouter(tags=["talks"])

MAX_AI_CALLS_PER_TALK = 60
HISTORY_TURNS = 8
SAFE_PLACEHOLDER = "(안전을 위해 저장하지 않은 말)"
STORY_DONE_LINE = "우리 대화로 이야기 한 편을 완성했어!"


# --- 직렬화 -----------------------------------------------------------------


def topic_out(topic: dict) -> TopicOut:
    return TopicOut(
        id=topic.get("id"),
        category=topic.get("category", "custom"),
        title=topic.get("title", ""),
        hook=topic.get("hook", ""),
        visual=topic.get("visual", "star"),
        source=topic.get("source", "bank"),
    )


def turn_out(turn: Turn) -> TurnOut:
    meta = turn.meta or {}
    return TurnOut(
        id=turn.id,
        seq=turn.seq,
        role=turn.role,  # type: ignore[arg-type]
        move=turn.move,
        text=turn.text,
        question=meta.get("question"),
        sentence_ok=turn.sentence_ok,
        input_mode=turn.input_mode,
        visual=meta.get("visual"),
        words=[WordNote(**w) for w in meta.get("words", [])],
        source=meta.get("source"),
        created_at=turn.created_at,
    )


def story_out(story: Story) -> StoryOut:
    return StoryOut(
        id=story.id,
        talk_id=story.talk_id,
        title=story.title,
        scenes=[StorySceneOut(**scene) for scene in story.scenes],
        ending_question=story.ending_question,
        source=story.source,
        created_at=story.created_at,
    )


def time_out(talk: Talk) -> TimeOut:
    minimum = get_settings().talk_min_seconds
    remaining = max(minimum - talk.active_seconds, 0)
    return TimeOut(
        active_seconds=talk.active_seconds,
        min_seconds=minimum,
        remaining_seconds=remaining,
        can_finish=remaining == 0 and talk.story_id is not None and talk.status == "active",
    )


def talk_turns(db: Session, talk: Talk) -> list[Turn]:
    return list(db.scalars(select(Turn).where(Turn.talk_id == talk.id).order_by(Turn.seq)))


def talk_out(db: Session, talk: Talk) -> TalkOut:
    turns = talk_turns(db, talk)
    pending = next((t.move for t in reversed(turns) if t.role == "friend"), "hook")
    story = db.get(Story, talk.story_id) if talk.story_id else None
    return TalkOut(
        id=talk.id,
        mode=talk.mode,
        category=talk.category,
        topic=topic_out(talk.topic),
        status=talk.status,
        pending_move=pending,
        time=time_out(talk),
        turns=[turn_out(t) for t in turns],
        story=story_out(story) if story else None,
    )


def talk_summary(talk: Talk) -> TalkSummary:
    return TalkSummary(
        id=talk.id,
        mode=talk.mode,
        category=talk.category,
        title=talk.topic.get("title", ""),
        status=talk.status,
        active_seconds=talk.active_seconds,
        story_id=talk.story_id,
        started_at=talk.started_at,
    )


# --- 내부 도우미 -------------------------------------------------------------


def _own_talk(db: Session, child: Child, talk_id: str) -> Talk:
    talk = db.get(Talk, talk_id)
    if talk is None or talk.child_id != child.id:
        raise HTTPException(status_code=404, detail="talk_not_found")
    return talk


def _add_turn(db: Session, talk: Talk, *, role: str, move: str, text: str, **fields) -> Turn:
    seq = (db.scalar(select(func.max(Turn.seq)).where(Turn.talk_id == talk.id)) or 0) + 1
    turn = Turn(talk_id=talk.id, seq=seq, role=role, move=move, text=text, **fields)
    db.add(turn)
    db.flush()
    return turn


def _profile(child: Child) -> dict:
    return {"nickname": child.nickname, "grade": child.grade, "likes": list(child.likes or [])}


def _glossary_words(text: str, topic: dict, limit: int = 3) -> list[dict]:
    entries = [(g["word"], g["meaning"]) for g in topic.get("glossary", [])] + list(bank.GLOSSARY.items())
    found: list[dict] = []
    for word, meaning in entries:
        if word in text and all(f["word"] != word for f in found):
            found.append({"word": word, "meaning": meaning, "example": ""})
    return found[:limit]


def _accepted(turns: list[Turn]) -> list[dict]:
    return [
        {"id": t.id, "move": t.move, "text": t.text, "seq": t.seq}
        for t in turns
        if t.role == "child" and t.sentence_ok
    ]


def _pending_question(turn: Turn | None, topic: dict) -> str:
    if turn is None:
        return topic.get("hook", "")
    return (turn.meta or {}).get("question") or turn.text


def _ai_budget(child: Child, talk: Talk) -> str | None:
    reason = ai_block_reason(child)
    if reason:
        return reason
    if talk.ai_calls >= MAX_AI_CALLS_PER_TALK:
        return "talk_call_limit"
    if not usage.try_consume(child.id):
        return "daily_limit"
    talk.ai_calls += 1
    return None


def _make_story(db: Session, child: Child, talk: Talk, turns: list[Turn]) -> Story:
    accepted = _accepted(turns)
    plot, source = None, "fallback"
    if _ai_budget(child, talk) is None:
        try:
            out = call_structured(
                purpose="talk.plot",
                instructions=prompt.plot_instructions(talk.mode),
                user_input=prompt.plot_input(child.nickname, talk.topic, accepted),
                schema=PlotLLM,
                max_output_tokens=3000,
            )
            plot = plots.validate_plot(
                [s.model_dump() for s in out.scenes],
                out.title,
                out.ending_question,
                {t["id"] for t in accepted},
                talk.topic,
            )
            source = "ai" if plot else "fallback"
        except LlmError:
            plot = None
    if plot is None:
        plot = plots.fallback_plot(child.nickname, talk.topic, accepted)
    story = Story(
        child_id=child.id,
        talk_id=talk.id,
        title=plot["title"],
        scenes=plot["scenes"],
        ending_question=plot["endingQuestion"],
        source=source,
        based_on_seq=max((t["seq"] for t in accepted), default=0),
    )
    db.add(story)
    db.flush()
    return story


def _validated_turn(out: TalkTurnLLM, topic: dict) -> tuple[str, str, str, list[dict]] | None:
    reaction, question = planner.clip(out.reaction, 80), planner.clip(out.question, 120)
    if not question or sensitive.detect(reaction) or sensitive.detect(question):
        return None
    spoken = f"{reaction} {question}"
    words = [
        {"word": w.word, "meaning": planner.clip(w.meaning, 80), "example": planner.clip(w.example, 100)}
        for w in out.hard_words[:3]
        if w.word and w.word in spoken and not sensitive.detect(f"{w.meaning} {w.example}")
    ]
    for extra in _glossary_words(spoken, topic):
        if len(words) < 3 and all(w["word"] != extra["word"] for w in words):
            words.append(extra)
    return reaction, question, out.visual, words


# --- 오늘의 주제 · 대화 시작 --------------------------------------------------


@router.get("/talks/today", response_model=TodayResponse)
def today(child: Child = Depends(require_child), db: Session = Depends(get_session)) -> TodayResponse:
    theme = bank.today_theme()
    recent = [
        t.topic.get("id")
        for t in db.scalars(select(Talk).where(Talk.child_id == child.id).order_by(Talk.started_at.desc()).limit(20))
    ]
    order = [theme["category"], *(c for c in ("science", "math", "history", "thinking") if c != theme["category"])]
    suggestions = []
    for category in order:
        topic = bank.pick_topic(category, recent)
        if topic:
            suggestions.append(topic_out(bank.snapshot(topic)))
        if len(suggestions) == 3:
            break
    return TodayResponse(theme=theme, suggestions=suggestions, categories=category_list(db, child))


@router.post("/talks", response_model=TalkOut, status_code=201)
def start_talk(
    req: TalkStartRequest, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> TalkOut:
    theme = bank.today_theme()
    recent = [
        t.topic.get("id")
        for t in db.scalars(select(Talk).where(Talk.child_id == child.id).order_by(Talk.started_at.desc()).limit(20))
    ]
    custom_category_id = shared_item_id = None

    if req.shared_item_id:
        item = db.get(SharedItem, req.shared_item_id)
        circles = sharing.circle_ids(db, child.family_id)
        if item is None or item.kind != "adventure" or not sharing.is_visible(item, child.family_id, circles):
            raise HTTPException(status_code=404, detail="adventure_not_found")
        if item.family_id != child.family_id and not (child.permissions or {}).get("browse_shared"):
            raise HTTPException(status_code=403, detail="permission_required:browse_shared")
        topic = bank.adventure_snapshot(item.body, item.title)
        category = topic["category"] if topic["category"] in bank.CATEGORIES else "custom"
        shared_item_id = item.id
    elif req.custom_category_id:
        custom = db.get(Category, req.custom_category_id)
        if custom is None or custom.child_id != child.id:
            raise HTTPException(status_code=404, detail="category_not_found")
        if req.custom_topic:
            if sensitive.detect(req.custom_topic.title) or sensitive.detect(req.custom_topic.hook):
                raise HTTPException(status_code=422, detail="unsafe_topic")
            topic = bank.custom_snapshot(custom.name, req.custom_topic.title, req.custom_topic.hook)
        else:
            topic = bank.custom_snapshot(custom.name, custom.name, f"'{custom.name}'에 대해 네가 제일 궁금한 건 뭐야?")
        category, custom_category_id = "custom", custom.id
    else:
        category = "diary" if req.mode == "diary" else (req.category or theme["category"])
        if category not in bank.CATEGORIES:
            raise HTTPException(status_code=422, detail="unknown_category")
        chosen = bank.get_topic(req.topic_id) if req.topic_id else None
        if req.topic_id and chosen is None:
            raise HTTPException(status_code=404, detail="topic_not_found")
        if chosen is None:
            chosen = bank.pick_topic("thinking" if category == "custom" else category, recent)
        assert chosen is not None
        topic, category = bank.snapshot(chosen), chosen.category

    now = clock.now()
    talk = Talk(
        child_id=child.id,
        mode="diary" if category == "diary" else "topic",
        category=category,
        custom_category_id=custom_category_id,
        shared_item_id=shared_item_id,
        topic=topic,
        started_at=now,
        last_turn_at=now,
    )
    db.add(talk)
    db.flush()
    theme_title = (
        theme["title"] if category == theme["category"] else f"{bank.CATEGORIES[category]['name']} 이야기 시간"
    )
    hook = planner.hook_line(child.nickname, str(theme_title), topic)
    _add_turn(
        db,
        talk,
        role="friend",
        move="hook",
        text=hook,
        meta={
            "visual": topic.get("visual", "star"),
            "words": _glossary_words(hook, topic),
            "source": topic.get("source", "bank"),
            "question": topic.get("hook", ""),
        },
    )
    db.commit()
    return talk_out(db, talk)


@router.get("/talks", response_model=list[TalkSummary])
def list_talks(child: Child = Depends(require_child), db: Session = Depends(get_session)) -> list[TalkSummary]:
    talks = db.scalars(select(Talk).where(Talk.child_id == child.id).order_by(Talk.started_at.desc()))
    return [talk_summary(t) for t in talks]


@router.get("/talks/{talk_id}", response_model=TalkOut)
def get_talk(talk_id: str, child: Child = Depends(require_child), db: Session = Depends(get_session)) -> TalkOut:
    return talk_out(db, _own_talk(db, child, talk_id))


# --- 대화 한 번 ---------------------------------------------------------------


@router.post("/talks/{talk_id}/turns", response_model=TurnResponse)
def take_turn(
    talk_id: str, req: TurnRequest, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> TurnResponse:
    talk = _own_talk(db, child, talk_id)
    if talk.status != "active":
        raise HTTPException(status_code=409, detail="talk_completed")
    now = clock.now()
    talk.active_seconds += planner.active_delta(talk.last_turn_at, now)
    talk.last_turn_at = now

    turns = talk_turns(db, talk)
    pending_turn = next((t for t in reversed(turns) if t.role == "friend"), None)
    pending_move = pending_turn.move if pending_turn else "hook"
    question = _pending_question(pending_turn, talk.topic)
    text = req.text.strip()
    topic = talk.topic

    def reply(
        child_turn: Turn,
        friend_turn: Turn,
        *,
        accepted: bool,
        move: str,
        ai: bool = False,
        error: str | None = None,
        safety: str | None = None,
        story: Story | None = None,
        starters: list[str] | None = None,
    ) -> TurnResponse:
        db.commit()
        return TurnResponse(
            ai=ai,
            error=error,
            accepted=accepted,
            safety=safety,
            move=move,
            child_turn=turn_out(child_turn),
            friend_turn=turn_out(friend_turn),
            sentence_starters=starters if starters is not None else starters_for(move),
            time=time_out(talk),
            story=story_out(story) if story else None,
        )

    def redirect(category: str, escalate: bool, line: str) -> TurnResponse:
        # 민감한 말은 원문을 저장하지 않는다. 자해 신호는 보호자에게 알릴 사건으로 남긴다.
        child_turn = _add_turn(
            db, talk, role="child", move=pending_move, text=SAFE_PLACEHOLDER, input_mode=req.input_mode,
            meta={"safety": category},
        )  # fmt: skip
        db.add(SafetyEvent(child_id=child.id, talk_id=talk.id, category=category, escalate=escalate))
        friend_turn = _add_turn(
            db, talk, role="friend", move=pending_move, text=line,
            meta={"visual": "heart", "source": "rule", "question": question, "words": []},
        )  # fmt: skip
        return reply(child_turn, friend_turn, accepted=False, move=pending_move, safety=category)

    flag = sensitive.detect(text)
    if flag:
        return redirect(flag.category, flag.escalate, flag.redirect)

    check = check_sentence(text)
    masked = pii.mask(text).text
    if not check.ok:
        child_turn = _add_turn(
            db, talk, role="child", move=pending_move, text=masked, sentence_ok=False, input_mode=req.input_mode,
            meta={"reason": check.reason},
        )  # fmt: skip
        friend_turn = _add_turn(
            db, talk, role="friend", move=pending_move, text=EXPAND_LINES[check.reason or "too_short"],
            meta={"visual": "thinking", "source": "rule", "question": question, "words": []},
        )  # fmt: skip
        return reply(
            child_turn, friend_turn, accepted=False, move=pending_move,
            starters=starters_for(pending_move, check.reason),
        )  # fmt: skip

    ai_reason = ai_block_reason(child)
    if ai_reason is None:
        result = moderation.moderate(masked)
        if result.flagged:
            self_harm = any(c.startswith("self-harm") for c in result.categories)
            category = "self_harm" if self_harm else "moderation"
            return redirect(category, self_harm, sensitive.REDIRECTS[category])

    stance = stance_of(text)
    meta = {
        "reason_given": has_reason(text),
        "new_idea": bool(MORE.search(text)) or has_reason(text),
        "stance": stance if pending_move == "reason_check" else "none",
        "sentences": sentence_count(text),
        "source": "rule",
    }
    child_turn = _add_turn(
        db, talk, role="child", move=pending_move, text=masked, sentence_ok=True, input_mode=req.input_mode, meta=meta
    )
    turns.append(child_turn)

    story = None
    if pending_move == "compose":
        story = _make_story(db, child, talk, turns)
        talk.story_id = story.id
        move = "continue"
    else:
        move = planner.next_move(talk.mode, len(_accepted(turns)), talk.active_seconds, talk.story_id is not None)

    ai_used, error = False, None
    reaction = STORY_DONE_LINE if story else planner.fallback_reaction(text)
    next_question = planner.fallback_question(move, topic)
    visual, words = topic.get("visual", "star"), _glossary_words(next_question, topic)
    budget = _ai_budget(child, talk)
    if budget is None:
        history = [(t.role, t.text) for t in turns[-HISTORY_TURNS:]]
        try:
            out = call_structured(
                purpose="talk.turn",
                instructions=prompt.turn_instructions(talk.mode),
                user_input=prompt.turn_input(_profile(child), topic, history, move, masked),
                schema=TalkTurnLLM,
            )
            checked = _validated_turn(out, topic)
            if checked:
                ai_used = True
                reaction, next_question, visual, words = checked
                if story:
                    reaction = f"{STORY_DONE_LINE} {reaction}"
                child_turn.meta = {
                    **meta,
                    "reason_given": meta["reason_given"] or out.reason_given,
                    "new_idea": meta["new_idea"] or out.new_idea,
                    "stance": (out.stance if out.stance != "none" else meta["stance"])
                    if pending_move == "reason_check"
                    else "none",
                    "child_idea": planner.clip(out.child_idea, 80),
                    "source": "ai",
                }
            else:
                error = "output_replaced"
        except LlmError as exc:
            error = f"ai_failed:{exc.code}"
    else:
        error = budget

    friend_turn = _add_turn(
        db, talk, role="friend", move=move, text=f"{reaction} {next_question}",
        meta={"visual": visual, "words": words, "source": "ai" if ai_used else "rule", "question": next_question},
    )  # fmt: skip
    return reply(child_turn, friend_turn, accepted=True, move=move, ai=ai_used, error=error, story=story)


@router.post("/talks/{talk_id}/finish", response_model=TalkOut)
def finish_talk(talk_id: str, child: Child = Depends(require_child), db: Session = Depends(get_session)) -> TalkOut:
    talk = _own_talk(db, child, talk_id)
    if talk.status == "completed":
        return talk_out(db, talk)
    if talk.story_id is None:
        raise HTTPException(status_code=409, detail={"code": "compose_first"})
    remaining = get_settings().talk_min_seconds - talk.active_seconds
    if remaining > 0:
        raise HTTPException(status_code=409, detail={"code": "min_time", "remainingSeconds": remaining})
    turns = talk_turns(db, talk)
    story = db.get(Story, talk.story_id)
    last_seq = max((t["seq"] for t in _accepted(turns)), default=0)
    if story is None or last_seq > story.based_on_seq:
        # 이야기를 만든 뒤에 더 나눈 생각까지 담아 다시 만든다.
        talk.story_id = _make_story(db, child, talk, turns).id
    talk.status = "completed"
    talk.ended_at = clock.now()
    db.commit()
    return talk_out(db, talk)


# --- 보호자 확인 --------------------------------------------------------------


@router.get("/guardian/children/{child_id}/talks", response_model=list[TalkSummary])
def guardian_talks(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> list[TalkSummary]:
    child = guardian_child(child_id, family, db)
    talks = db.scalars(select(Talk).where(Talk.child_id == child.id).order_by(Talk.started_at.desc()))
    return [talk_summary(t) for t in talks]


@router.get("/guardian/children/{child_id}/talks/{talk_id}", response_model=TalkOut)
def guardian_talk(
    child_id: str, talk_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> TalkOut:
    return talk_out(db, _own_talk(db, guardian_child(child_id, family, db), talk_id))
