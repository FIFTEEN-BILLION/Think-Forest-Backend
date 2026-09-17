"""티키와 첫인사 — 자기소개를 듣고 별명·학년/나이·좋아하는 것·그 까닭·키우고 싶은 힘을 모은다.

- 첫 메시지는 정확히 `자기소개해볼까?`.
- 한 번에 부족한 항목 하나만 묻고, 채운 항목은 다시 묻지 않는다(순서는 FIELDS).
- AI 가 허용되면 `first_greeting.extract` 로 뽑고, 막히거나 실패하면
  기존 첫 만남 규칙(`talks/onboarding.py`)으로 뽑는다.
- 학교 이름은 저장하지 않는다. `schoolOrGroup` 은 종류(초등학교·홈스쿨·유치원·기타)만.
- 뽑은 값마다 원문 메시지 id 와 신뢰도를 conversation_facts 에 남긴다.
"""

from __future__ import annotations

import re

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import clock
from ..prompts import v1_conversation as prompt
from ..safety import topics as sensitive
from ..talks import onboarding as ob
from ..talks.planner import active_delta, clip
from . import ai_gate, chat, idempotency
from .cursor import iso
from .deps import CurrentUser
from .errors import ApiError
from .korean import call_name, has_batchim, josa, vocative
from .models import prefixed_id
from .models_conversation import ChildProfile, ConversationFact, ConversationSession
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
FIRST_MESSAGE = "자기소개해볼까?"
FIELDS: tuple[str, ...] = ("NICKNAME", "GRADE_OR_AGE", "INTEREST", "INTEREST_DETAIL", "GROWTH_GOAL")
SCHOOL_KINDS = {"elementary": "초등학교", "homeschool": "홈스쿨", "kindergarten": "유치원", "other": "기타"}
AFFILIATION_OF = {"초등학교": "elementary", "홈스쿨": "homeschool", "유치원": "other", "기타": "other"}

_YES = re.compile(r"^(응+|어+|웅+|네|예|그래|좋아|ㅇㅇ|오케이|알겠어|해\s*볼래|할래)[.!~\s]*$")
_GREETING = re.compile(r"^(안녕+|하이|헬로|반가워|hi|hello)", re.IGNORECASE)
_UNSURE = re.compile(r"^(몰라|모르겠|글쎄|음+[.!~\s]*$|그냥|없어|잘\s*모르)")
_AGE = re.compile(r"(열|아홉|여덟|일곱|여섯|다섯|1[0-3]|[4-9])\s*살")
_AGE_WORDS = {"다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
_CALL_ME = re.compile(r"([가-힣A-Za-z0-9]{1,10}?)(?:이)?라고\s*(?:불러|해\s*줘|부르)")
_NAMED = re.compile(
    r"(?:별명|이름)(?:은|이)?\s*([가-힣A-Za-z0-9]{1,10}?)(?:이야|야|이에요|예요|입니다|이고|[.!,~\s]|$)"
)
_CLAUSE = re.compile(r"[.!?\n]+|,\s*|(?<=[가-힣])이고\s+")
_LIST_JOIN = re.compile(r"\s*,\s*|(?<=[가-힣])(?:하고|이랑|랑)\s+|\s+그리고\s+")
_LIST_PREFIX = re.compile(r"^(?:나는|난|저는|내가|나도)\s+")
_NOT_NICKNAME = re.compile(r"학년|살|좋아|초등|유치원|홈스쿨|학교")
_GOAL = re.compile(r"^(.*?)\s*(?:을|를)?\s*(?:더\s*)?(키우고|잘하고|배우고|알고|하고)\s*싶")
_GOAL_SUFFIX = {"키우고": "", "잘하고": " 잘하기", "배우고": " 배우기", "알고": " 알아보기", "하고": " 하기"}
_GOAL_PREFIX = re.compile(r"^(?:음+\s*|나는\s*|난\s*|저는\s*|나도\s*|티키랑\s*|티키와\s*)+")


# --- 초안 · 준비 상태 ---------------------------------------------------------


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
    draft = empty_draft()
    for key in draft:
        if key in stored:
            draft[key] = list(stored[key]) if isinstance(stored[key], list) else stored[key]
    return draft


def missing_fields(draft: dict) -> list[str]:
    present = {
        "NICKNAME": bool(draft["nickname"]),
        "GRADE_OR_AGE": bool(draft["gradeOrAgeBand"]),
        "INTEREST": bool(draft["interests"]),
        "INTEREST_DETAIL": bool(draft["interestDetails"]),
        "GROWTH_GOAL": bool(draft["growthGoal"]),
    }
    return [f for f in FIELDS if not present[f]]


def readiness_out(draft: dict) -> GreetingReadiness:
    missing = missing_fields(draft)
    return GreetingReadiness(
        ready=not missing, progress=(len(FIELDS) - len(missing)) * 100 // len(FIELDS), missing=missing
    )


def draft_out(draft: dict) -> ProfileDraftOut:
    return ProfileDraftOut.model_validate(draft)


# --- 규칙 추출 ----------------------------------------------------------------


def _band(grade: int | None, age: int | None, school: str | None) -> str | None:
    if grade:
        return f"{grade}학년" if school == "홈스쿨" else f"초등학교 {grade}학년"
    if age:
        return f"{age}살"
    return None


def parse_age(text: str) -> int | None:
    hit = _AGE.search(text or "")
    if not hit:
        return None
    word = hit.group(1)
    return _AGE_WORDS.get(word) or int(word)


def parse_school(text: str) -> str | None:
    if "유치원" in (text or ""):
        return "유치원"
    kind = ob.parse_affiliation(text)
    return SCHOOL_KINDS.get(kind) if kind else None


def parse_nickname(text: str) -> str | None:
    for pattern in (_CALL_ME, _NAMED):
        hit = pattern.search(text or "")
        if hit and (nickname := ob.clean_nickname(hit.group(1))):
            return nickname
    first = _CLAUSE.split((text or "").strip())[0]
    if not first or _NOT_NICKNAME.search(first) or _YES.match(first) or _UNSURE.match(first) or _GREETING.match(first):
        return None
    return ob.parse_nickname(first)


# 기존 clean_items 는 끝의 조사(이·가 등)를 떼므로 '고양이'가 '고양'이 된다. 흔한 '~이' 낱말은 지킨다.
_KEEP_I = {"고양이", "원숭이", "거북이", "달팽이", "호랑이", "오이", "종이", "놀이", "아이", "강아지"}


def clean_items(items: list[str]) -> list[str]:
    kept: list[str] = []
    for item in items:
        word = re.sub(r"[^가-힣A-Za-z0-9 ]", "", item or "").strip()
        base = word[:-1] if word[-1:] in ("을", "를", "이", "가", "은", "는", "도") else word
        if word in _KEEP_I or word.endswith("놀이"):
            kept.append(word)
        elif base in _KEEP_I or base.endswith("놀이"):
            kept.append(base)
        else:
            kept.extend(ob.clean_items([word]))
    out: list[str] = []
    for word in kept:
        if word and word not in ob._STOP and len(word) <= 15 and word not in out and not sensitive.detect(word):
            out.append(word)
    return out[:5]


def parse_list(text: str) -> list[str]:
    """'그림 그리기랑 강아지'처럼 이음말(랑·하고·그리고·쉼표)로만 나눠 여러 낱말 관심사를 지킨다.

    이음말이 없으면 기존 첫 만남 규칙(`ob.parse_list`)처럼 띄어쓰기로 나눈다.
    """
    tail_removed = _LIST_PREFIX.sub("", ob._LIKE_TAIL.sub("", (text or "").strip()))
    parts = [p.strip() for p in _LIST_JOIN.split(tail_removed) if p and p.strip()]
    if len(parts) <= 1:
        parts = [p for p in ob._LIST_SPLIT.split(tail_removed) if p]
    return clean_items(parts)


def parse_interests(text: str, *, asked: bool) -> list[str]:
    clauses = [c for c in _CLAUSE.split(text or "") if c and "좋아" in c and not _YES.match(c.strip())]
    found = [item for c in clauses for item in parse_list(c)]
    if not found and asked and not _UNSURE.match(text or ""):
        found = parse_list(text)
    return clean_items([i for i in found if "좋아" not in i and "싶" not in i])


def clean_detail(text: str) -> str | None:
    line = clip(text or "", 60)
    if len(line.replace(" ", "")) < 4 or _UNSURE.match(line) or sensitive.detect(line):
        return None
    return line


def clean_goal(text: str) -> str | None:
    t = _GOAL_PREFIX.sub("", " ".join((text or "").split()))
    hit = _GOAL.search(t)
    if hit and hit.group(1).strip():
        t = hit.group(1).strip() + _GOAL_SUFFIX[hit.group(2)]
    t = re.sub(r"\s*(이야|야|요|이에요|예요)?[.!~\s]*$", "", t)
    goal = clip(t, 30)
    if len(goal.replace(" ", "")) < 2 or _UNSURE.match(goal) or _YES.match(goal) or sensitive.detect(goal):
        return None
    return goal


def apply_rules(draft: dict, asked: str | None, original: str, masked: str) -> dict:
    """AI 를 쓸 수 없을 때. 학년·나이·소속 종류는 원문 숫자에서, 나머지는 가린 문장에서 읽는다."""
    updated = {**draft, "interests": list(draft["interests"]), "interestDetails": list(draft["interestDetails"])}
    school = parse_school(original) or updated["schoolOrGroup"]
    band = _band(ob.parse_grade(original), parse_age(original), school)
    if band:
        updated["gradeOrAgeBand"] = band
    if school:
        updated["schoolOrGroup"] = school
    if asked == "NICKNAME" or _CALL_ME.search(masked) or _NAMED.search(masked):
        updated["nickname"] = parse_nickname(masked) or updated["nickname"]
    if not updated["interests"] or asked == "INTEREST":
        updated["interests"] = clean_items([*updated["interests"], *parse_interests(masked, asked=asked == "INTEREST")])
    if asked == "INTEREST_DETAIL" and (detail := clean_detail(masked)):
        updated["interestDetails"] = [*updated["interestDetails"], detail][:3]
    elif updated["interests"] and not updated["interestDetails"]:
        # "공룡이 좋아. 크고 멋있으니까"처럼 까닭을 함께 말하면 그 부분을 담는다.
        reason = next((c for c in _CLAUSE.split(masked) if c and re.search(r"니까|때문|거든|어서|아서|해서", c)), None)
        if reason and (detail := clean_detail(reason)):
            updated["interestDetails"] = [detail]
    if asked == "GROWTH_GOAL" and (goal := clean_goal(masked)):
        updated["growthGoal"] = goal
    return updated


def merge_ai(draft: dict, out: FirstGreetingLLM, original: str) -> dict:
    updated = {**draft, "interests": list(draft["interests"]), "interestDetails": list(draft["interestDetails"])}
    if nickname := ob.clean_nickname(out.nickname):
        updated["nickname"] = nickname
    school = parse_school(original) or (SCHOOL_KINDS.get(out.affiliation) if out.affiliation else None)
    school = school or updated["schoolOrGroup"]
    grade = ob.parse_grade(original) or (out.grade if out.grade and 1 <= out.grade <= 6 else None)
    age = parse_age(original) or (out.age if out.age and 5 <= out.age <= 13 else None)
    if band := _band(grade, age, school):
        updated["gradeOrAgeBand"] = band
    if school:
        updated["schoolOrGroup"] = school
    updated["interests"] = clean_items([*updated["interests"], *out.interests])
    details = [d for d in (clean_detail(x) for x in out.interest_details) if d and d not in updated["interestDetails"]]
    updated["interestDetails"] = [*updated["interestDetails"], *details][:3]
    if out.growth_goal and (goal := clean_goal(out.growth_goal)):
        updated["growthGoal"] = goal
    return updated


# --- 티키 대사 ----------------------------------------------------------------


def question_for(field: str | None, draft: dict) -> str:
    if field == "NICKNAME":
        return "티키가 너를 뭐라고 부르면 좋을까? 별명도 좋아!"
    if field == "GRADE_OR_AGE":
        return "너는 몇 학년이야? 몇 살인지 말해 줘도 좋아."
    if field == "INTEREST":
        return "너는 뭘 좋아해? 놀이, 동물, 음식 뭐든 괜찮아!"
    if field == "INTEREST_DETAIL":
        interest = draft["interests"][0] if draft["interests"] else "그거"
        return f"{josa(interest, '이', '가')} 왜 좋아? 기억나는 일이 있으면 들려줘!"
    if field == "GROWTH_GOAL":
        return "마지막 질문! 티키랑 이야기하면서 뭘 더 잘하고 싶어? ‘질문하기’, ‘내 생각 말하기’처럼 말해 줘."
    return (
        f"고마워, {vocative(draft['nickname'])}! 이제 티키가 너를 잘 알 것 같아. "
        "더 하고 싶은 말이 있으면 해 줘. 다 됐으면 마치기 버튼을 눌러 줘."
    )


def reaction_for(before: dict, after: dict, text: str, asked: str | None) -> str:
    nickname = after["nickname"]
    if _UNSURE.match(text):
        return "괜찮아, 천천히 생각해도 돼."
    if new := [i for i in after["interests"] if i not in before["interests"]]:
        who = f"{call_name(nickname)}는 " if nickname else ""
        return f"{who}{josa(new[0], '을', '를')} 좋아하는구나!"
    if after["growthGoal"] and after["growthGoal"] != before["growthGoal"]:
        return f"‘{after['growthGoal']}’, 티키가 같이 도와줄게!"
    if len(after["interestDetails"]) > len(before["interestDetails"]):
        return "그래서 좋아하는구나!"
    if after["gradeOrAgeBand"] and after["gradeOrAgeBand"] != before["gradeOrAgeBand"]:
        return f"{josa(after['gradeOrAgeBand'], '이구나', '구나')}!"
    if nickname and nickname != before["nickname"]:
        return f"만나서 반가워, {vocative(nickname)}!"
    if _YES.match(text):
        return "좋아!"
    if _GREETING.match(text):
        return "안녕! 티키도 반가워."
    if asked and asked in missing_fields(after):
        return "티키가 잘 못 알아들었어."
    return "그렇구나!"


# --- 세션 ---------------------------------------------------------------------


def session_out(
    db: Session,
    session: ConversationSession,
    *,
    resumed: bool = False,
    message_cursor: str | None = None,
    limit: int | None = None,
) -> GreetingSessionOut:
    messages, next_cursor = chat.page_messages(db, session, message_cursor, limit)
    draft = draft_of(session)
    return GreetingSessionOut(
        session_id=session.id,
        status=session.status,
        resumed=resumed,
        messages=messages,
        next_cursor=next_cursor,
        current_interaction=chat.interaction_out(session.current_interaction),
        profile_draft=draft_out(draft),
        readiness=readiness_out(draft),
    )


def start_or_resume(db: Session, cu: CurrentUser) -> GreetingSessionOut:
    existing = db.scalar(
        select(ConversationSession)
        .where(
            ConversationSession.user_id == cu.id,
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
    data = chat.interaction("ask", FIRST_MESSAGE, field="NICKNAME")
    chat.add_assistant(db, session, FIRST_MESSAGE, "fallback", data)
    session.current_interaction = data
    return session_out(db, session)


_FACT_KEYS = (
    ("NICKNAME", "nickname"),
    ("SCHOOL_OR_GROUP", "schoolOrGroup"),
    ("GRADE_OR_AGE", "gradeOrAgeBand"),
    ("INTEREST", "interests"),
    ("INTEREST_DETAIL", "interestDetails"),
    ("GROWTH_GOAL", "growthGoal"),
)


def _record_facts(
    db: Session, session: ConversationSession, before: dict, after: dict, message_id: str, confidence: float
):
    for field, key in _FACT_KEYS:
        if after[key] == before[key]:
            continue
        db.execute(
            update(ConversationFact)
            .where(ConversationFact.session_id == session.id, ConversationFact.field == field)
            .values(current=False)
        )
        db.add(
            ConversationFact(
                session_id=session.id,
                field=field,
                value=after[key],
                source_message_id=message_id,
                confidence=confidence,
                current=True,
                created_at=clock.now(),
            )
        )


def handle_message(db: Session, cu: CurrentUser, session: ConversationSession, req: MessageRequest):
    current, option_id, raw = chat.resolve_input(session, req)
    before = draft_of(session)
    asked = (current or {}).get("field")
    masked = raw if option_id else chat.screen(db, cu, raw, allow_personal_info=True, names=False)[0]

    now = clock.now()
    session.active_seconds += active_delta(session.last_message_at, now)
    session.last_message_at = now
    options = (current or {}).get("options") or []
    user_message = chat.add_message(
        db,
        session,
        role="USER",
        content=masked,
        source="child",
        question_id=(current or {}).get("questionId"),
        answer={"type": req.input.type, "optionId": option_id, "options": options if option_id else []},
        client_message_id=req.client_message_id,
        meta={"field": asked},
    )

    draft, source, reaction, ai_question = before, "fallback", None, None
    if option_id:
        end = "clear" if option_id == "END" else "none"
        if option_id == "CONTINUE":
            reaction = "좋아, 계속 이야기하자!"
    else:
        end = chat.end_intent(raw)
        if end != "clear" and ai_gate.budget(cu.child, session) is None:
            try:
                out = ai_gate.call(
                    purpose="first_greeting.extract",
                    instructions=prompt.greeting_instructions(),
                    user_input=prompt.greeting_input(before, missing_fields(before), masked),
                    schema=FirstGreetingLLM,
                )
                draft, source = merge_ai(before, out, raw), "ai"
                end = chat.combine_end_intent(end, out.end_intent)
                reaction = chat.safe_line(out.reaction, 60)
                question = chat.safe_line(out.question, 80)
                if question and question.endswith("?"):
                    ai_question = (out.asked_field, question)
            except ai_gate.LlmError:
                source = "fallback"
        if source == "fallback" and end == "none":
            draft = apply_rules(before, asked, raw, masked)
        _record_facts(db, session, before, draft, user_message.id, 0.8 if source == "ai" else 0.6)
    session.readiness = {"draft": draft}
    missing = missing_fields(draft)

    completion, next_data = None, None
    if end == "clear" and not missing:
        content = f"좋아, {vocative(draft['nickname'])}! 알려 준 것 잘 기억할게. 다음에 또 이야기하자!"
    elif end == "clear":
        question = question_for(missing[0], draft)
        content = f"아직 티키가 기억하고 싶은 게 하나 있어! {question}"
        next_data = chat.interaction("ask", question, field=missing[0])
    elif end == "unsure":
        content = "조금 쉬고 싶어? 오늘은 여기까지 할지 골라 줘."
        next_data = chat.interaction("confirm_end", content, chat.END_OPTIONS, field=missing[0] if missing else None)
    else:
        field = missing[0] if missing else None
        question = ai_question[1] if field and ai_question and ai_question[0] == field else question_for(field, draft)
        if reaction is None:
            reaction = reaction_for(before, draft, raw if not option_id else "", asked)
        content = f"{reaction} {question}".strip()
        next_data = chat.interaction("ask", question, field=field)

    assistant = chat.add_assistant(db, session, content, source, next_data)
    session.current_interaction = next_data
    session.status = "READY_TO_FINISH" if not missing else "ACTIVE"
    session.updated_at = now
    if end == "clear" and not missing:
        completion = complete(db, cu, session)
    response = GreetingMessageResponse(
        user_message=chat.message_out(user_message),
        assistant_message=chat.message_out(assistant),
        next_interaction=chat.interaction_out(next_data),
        profile_draft=draft_out(draft),
        readiness=readiness_out(draft),
        status=session.status,
        end_intent_detected=end == "clear",
        completion=completion,
    )
    idempotency.remember(db, cu.id, req.client_message_id, chat.message_route(session), response)
    db.commit()
    return response


# --- 완료 ---------------------------------------------------------------------


def _summary(draft: dict) -> str:
    who = call_name(draft["nickname"])
    interests = ", ".join(draft["interests"][:3])
    goal = draft["growthGoal"]
    goal_obj = f"‘{goal}’{'을' if has_batchim(goal) else '를'}"
    return f"{who}는 {josa(interests, '을', '를')} 좋아하고, 티키와 함께 {goal_obj} 키워 가고 싶어 해요."


def complete(db: Session, cu: CurrentUser, session: ConversationSession) -> GreetingCompletion:
    """완료. commit 은 호출자가 한다. 이미 완료된 세션은 처음 결과를 그대로 돌려준다."""
    if session.status == "COMPLETED" and session.result:
        return GreetingCompletion.model_validate(session.result)
    chat.ensure_open(session)
    draft = draft_of(session)
    missing = missing_fields(draft)
    if missing:
        raise ApiError(409, "FIRST_GREETING_NOT_READY", "티키가 조금 더 알고 싶은 게 있어요.", {"missing": missing})
    session.status = "FINALIZING"
    db.flush()

    now = clock.now()
    summary = _summary(draft)
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

    # 기존 대화 엔진(/talks) 개인화용으로 아이 행도 맞춘다. 학년은 숫자로 읽힐 때만.
    child = cu.child
    child.nickname = draft["nickname"][:20]
    child.likes = list(draft["interests"][:5])
    child.want_to_learn = [draft["growthGoal"]]
    child.profile_confirmed = True
    if grade := re.search(r"([1-6])학년", draft["gradeOrAgeBand"] or ""):
        child.grade = int(grade.group(1))
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
