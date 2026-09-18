"""v1 티키와 첫인사 — 첫 질문·이어하기·한 항목씩 묻기·완료와 프로필 저장·종료 의도·멱등·안전·AI 경로·소유권."""

from __future__ import annotations

import itertools

from app import db
from app.models import Child, SafetyEvent
from app.v1 import ai_gate
from app.v1.models_conversation import ChildProfile, ConversationFact, ConversationMessage
from app.v1.schemas_conversation import FirstGreetingLLM
from sqlalchemy import select

from conftest import tick
from test_v1_conversations import make_user

BASE = "/api/v1/first-greeting/sessions"
_ids = itertools.count(1)


def begin(client, user: dict, key: str | None = None) -> dict:
    headers = {**user["headers"], **({"Idempotency-Key": key} if key else {})}
    res = client.post(BASE, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def reply(client, user: dict, session_id: str, text: str | None = None, option: str | None = None, cmid: str | None = None):
    body = {"clientMessageId": cmid or f"fg-{next(_ids)}"}
    body["input"] = {"type": "SINGLE_CHOICE", "optionId": option} if option else {"type": "TEXT", "text": text}
    return client.post(f"{BASE}/{session_id}/messages", json=body, headers=user["headers"])


def fill_profile(client, user: dict, session_id: str, frozen: dict) -> list[dict]:
    answers = [
        "나는 별이라고 불러 줘. 2학년이고 공룡을 좋아해.",
        "티라노사우루스 이빨이 엄청 커서 멋있어",
        "궁금한 걸 질문하는 힘을 키우고 싶어",
    ]
    bodies = []
    for text in answers:
        tick(frozen, 20)
        res = reply(client, user, session_id, text)
        assert res.status_code == 200, res.text
        bodies.append(res.json())
    return bodies


def test_fallback_first_greeting_asks_one_item_at_a_time_and_completes(client, frozen):
    user = make_user()
    start = begin(client, user, key="fg-start")
    assert start["status"] == "ACTIVE" and start["resumed"] is False
    assert [m["content"] for m in start["messages"]] == ["자기소개해볼까?"]
    assert start["messages"][0]["role"] == "ASSISTANT"
    assert start["readiness"] == {
        "ready": False, "progress": 0, "missing": ["NICKNAME", "GRADE_OR_AGE", "INTEREST", "INTEREST_DETAIL", "GROWTH_GOAL"]
    }  # fmt: skip
    assert start["profileDraft"] == {
        "nickname": None, "schoolOrGroup": None, "gradeOrAgeBand": None, "interests": [], "interestDetails": [],
        "growthGoal": None,
    }  # fmt: skip
    resumed = begin(client, user)
    assert resumed["sessionId"] == start["sessionId"] and resumed["resumed"] is True
    assert begin(client, user, key="fg-start") == start  # 같은 Idempotency-Key 는 처음 응답 그대로
    session_id = start["sessionId"]

    first, detail, goal = fill_profile(client, user, session_id, frozen)
    assert first["profileDraft"]["nickname"] == "별"
    assert first["profileDraft"]["gradeOrAgeBand"] == "초등학교 2학년"
    assert first["profileDraft"]["interests"] == ["공룡"]
    assert first["readiness"] == {"ready": False, "progress": 60, "missing": ["INTEREST_DETAIL", "GROWTH_GOAL"]}
    assert first["assistantMessage"]["content"] == "별이는 공룡을 좋아하는구나! 공룡이 왜 좋아? 기억나는 일이 있으면 들려줘!"
    assert first["nextInteraction"]["type"] == "TEXT" and first["status"] == "ACTIVE"
    assert first["endIntentDetected"] is False and first["completion"] is None

    assert detail["profileDraft"]["interestDetails"] == ["티라노사우루스 이빨이 엄청 커서 멋있어"]
    assert detail["readiness"]["missing"] == ["GROWTH_GOAL"]
    assert "마지막 질문!" in detail["assistantMessage"]["content"]

    assert goal["profileDraft"]["growthGoal"] == "궁금한 걸 질문하는 힘"
    assert goal["status"] == "READY_TO_FINISH" and goal["readiness"] == {"ready": True, "progress": 100, "missing": []}
    assert "마치기 버튼" in goal["assistantMessage"]["content"]

    tick(frozen, 20)
    more = reply(client, user, session_id, "나는 수영도 잘해").json()  # 준비된 뒤에도 계속 이야기할 수 있다
    assert more["status"] == "READY_TO_FINISH" and more["completion"] is None

    headers = {**user["headers"], "Idempotency-Key": "fg-done"}
    done = client.post(f"{BASE}/{session_id}/complete", json={"trigger": "BUTTON"}, headers=headers)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "COMPLETED" and body["completedAt"].endswith("Z")
    assert body["profile"]["id"].startswith("prf_")
    assert {k: body["profile"][k] for k in ("nickname", "gradeOrAgeBand", "interests", "interestDetails", "growthGoal")} == {
        "nickname": "별", "gradeOrAgeBand": "초등학교 2학년", "interests": ["공룡"],
        "interestDetails": ["티라노사우루스 이빨이 엄청 커서 멋있어"], "growthGoal": "궁금한 걸 질문하는 힘",
    }  # fmt: skip
    assert body["summary"] == "별이는 공룡을 좋아하고, 티키와 함께 ‘궁금한 걸 질문하는 힘’을 키워 가고 싶어 해요."
    assert client.post(f"{BASE}/{session_id}/complete", json={}, headers=headers).json() == body
    assert client.post(f"{BASE}/{session_id}/complete", headers=user["headers"]).json() == body

    me = client.get("/api/v1/me", headers=user["headers"]).json()
    assert me["user"] == {"id": user["id"], "role": "CHILD", "needsFirstGreeting": False}
    assert me["profile"] == {
        "id": body["profile"]["id"], "nickname": "별", "gradeOrAgeBand": "초등학교 2학년", "interests": ["공룡"],
        "growthGoal": "궁금한 걸 질문하는 힘",
    }  # fmt: skip
    with next(db.get_session()) as session:
        child = session.get(Child, user["child_id"])
        assert (child.nickname, child.grade, child.likes, child.want_to_learn, child.profile_confirmed) == (
            "별", 2, ["공룡"], ["궁금한 걸 질문하는 힘"], True
        )  # fmt: skip
        assert session.scalar(select(ChildProfile).where(ChildProfile.child_id == user["child_id"])).version == 1
        facts = session.scalars(select(ConversationFact).where(ConversationFact.current.is_(True))).all()
        assert {f.field for f in facts} >= {"NICKNAME", "GRADE_OR_AGE", "INTEREST", "INTEREST_DETAIL", "GROWTH_GOAL"}
        assert all(f.source_message_id and f.source_message_id.startswith("msg_") for f in facts)

    closed = reply(client, user, session_id, "하나 더 있어")
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "SESSION_CLOSED"
    restored = client.get(f"{BASE}/{session_id}", headers=user["headers"]).json()
    assert restored["status"] == "COMPLETED" and restored["currentInteraction"] is None


def test_complete_before_ready_returns_missing(client, frozen):
    user = make_user()
    session_id = begin(client, user)["sessionId"]
    reply(client, user, session_id, "내 별명은 하늘이야")
    r = client.post(f"{BASE}/{session_id}/complete", json={"trigger": "BUTTON"}, headers=user["headers"])
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "FIRST_GREETING_NOT_READY" and error["requestId"].startswith("req_")
    assert error["details"]["missing"] == ["GRADE_OR_AGE", "INTEREST", "INTEREST_DETAIL", "GROWTH_GOAL"]


def test_step_by_step_answers_do_not_save_school_name(client, frozen):
    user = make_user()
    session_id = begin(client, user)["sessionId"]
    yes = reply(client, user, session_id, "응!").json()
    assert yes["profileDraft"]["nickname"] is None
    assert yes["assistantMessage"]["content"] == "좋아! 티키가 너를 뭐라고 부르면 좋을까? 별명도 좋아!"
    nick = reply(client, user, session_id, "내 별명은 하늘이야").json()
    assert nick["profileDraft"]["nickname"] == "하늘"
    assert nick["assistantMessage"]["content"].startswith("만나서 반가워, 하늘아! 너는 몇 학년이야?")
    grade = reply(client, user, session_id, "나는 햇살초등학교 3학년이야").json()
    assert grade["profileDraft"]["gradeOrAgeBand"] == "초등학교 3학년"
    assert grade["profileDraft"]["schoolOrGroup"] == "초등학교"
    assert "햇살" not in grade["userMessage"]["content"]
    likes = reply(client, user, session_id, "나는 축구랑 고양이를 좋아해").json()
    assert likes["profileDraft"]["interests"] == ["축구", "고양이"]
    assert likes["assistantMessage"]["content"].startswith("하늘이는 축구를 좋아하는구나! 축구가 왜 좋아?")
    unsure = reply(client, user, session_id, "몰라").json()
    assert unsure["assistantMessage"]["content"].startswith("괜찮아, 천천히 생각해도 돼. 축구가 왜 좋아?")
    age = make_user()
    sid = begin(client, age)["sessionId"]
    reply(client, age, sid, "별이야")
    assert reply(client, age, sid, "여덟 살이야").json()["profileDraft"]["gradeOrAgeBand"] == "8살"
    with next(db.get_session()) as session:
        assert not any("햇살" in c for c in session.scalars(select(ConversationMessage.content)).all())


def test_end_intent_completes_when_ready_or_asks_one_more(client, frozen):
    user = make_user()
    session_id = begin(client, user)["sessionId"]
    early = reply(client, user, session_id, "그만할래").json()
    assert early["endIntentDetected"] is True and early["status"] == "ACTIVE" and early["completion"] is None
    assert early["assistantMessage"]["content"] == "아직 티키가 기억하고 싶은 게 하나 있어! 티키가 너를 뭐라고 부르면 좋을까? 별명도 좋아!"  # noqa: E501
    assert early["profileDraft"]["nickname"] is None

    unsure = reply(client, user, session_id, "졸려").json()
    assert unsure["nextInteraction"]["type"] == "SINGLE_CHOICE"
    assert unsure["nextInteraction"]["options"] == [{"id": "END", "label": "응, 그만할래"}, {"id": "CONTINUE", "label": "아니, 더 할래"}]  # noqa: E501
    mismatch = reply(client, user, session_id, option="MAYBE")
    assert mismatch.status_code == 409 and mismatch.json()["error"]["code"] == "QUESTION_MISMATCH"
    cont = reply(client, user, session_id, option="CONTINUE").json()
    assert cont["assistantMessage"]["content"] == "좋아, 계속 이야기하자! 티키가 너를 뭐라고 부르면 좋을까? 별명도 좋아!"
    assert cont["nextInteraction"]["type"] == "TEXT"

    fill_profile(client, user, session_id, frozen)
    tick(frozen, 10)
    finished = reply(client, user, session_id, "이제 그만하자").json()
    assert finished["endIntentDetected"] is True and finished["status"] == "COMPLETED"
    assert finished["nextInteraction"] is None
    assert finished["completion"]["profile"]["nickname"] == "별"
    assert client.get("/api/v1/me", headers=user["headers"]).json()["user"]["needsFirstGreeting"] is False
    assert begin(client, user)["resumed"] is False  # 완료 뒤에는 새 세션


def test_client_message_id_replay_and_unsafe_content(client, frozen):
    user = make_user()
    session_id = begin(client, user)["sessionId"]
    first = reply(client, user, session_id, "나는 별이야", cmid="same-1")
    second = reply(client, user, session_id, "나는 달이야", cmid="same-1")
    assert first.json() == second.json() and second.json()["profileDraft"]["nickname"] == "별"
    detail = client.get(f"{BASE}/{session_id}?limit=2", headers=user["headers"]).json()
    assert len(detail["messages"]) == 2 and detail["nextCursor"] == detail["messages"][0]["id"]
    older = client.get(f"{BASE}/{session_id}?messageCursor={detail['nextCursor']}", headers=user["headers"]).json()
    assert [m["content"] for m in older["messages"]] == ["자기소개해볼까?"]

    unsafe = reply(client, user, session_id, "대통령 선거 얘기 하자")
    assert unsafe.status_code == 422 and unsafe.json()["error"]["code"] == "UNSAFE_CONTENT"
    with next(db.get_session()) as session:
        assert not any("대통령" in c for c in session.scalars(select(ConversationMessage.content)).all())
        assert [e.category for e in session.scalars(select(SafetyEvent)).all()] == ["politics"]
    assert len(client.get(f"{BASE}/{session_id}", headers=user["headers"]).json()["messages"]) == 3


def test_other_user_cannot_read_or_answer_session(client, frozen):
    owner, other = make_user(), make_user()
    session_id = begin(client, owner)["sessionId"]
    assert client.get(f"{BASE}/{session_id}", headers=other["headers"]).json()["error"]["code"] == "SESSION_NOT_FOUND"
    assert reply(client, other, session_id, "나는 별이야").status_code == 404
    assert client.post(f"{BASE}/{session_id}/complete", headers=other["headers"]).status_code == 404
    assert begin(client, other)["sessionId"] != session_id
    assert client.post(BASE).status_code == 401


def test_ai_extraction_uses_ai_question_only_for_the_next_missing_item(client, frozen, monkeypatch):
    monkeypatch.setattr(ai_gate, "ai_block_reason", lambda child: None)
    outputs = [
        FirstGreetingLLM(
            nickname="별", grade=2, age=None, affiliation="elementary", interests=["공룡"], interest_details=[],
            growth_goal=None, end_intent="none", reaction="별이는 공룡을 좋아하는구나!",
            question="어떤 공룡이 제일 좋고, 왜 좋아?", asked_field="INTEREST_DETAIL",
        ),
        FirstGreetingLLM(
            nickname=None, grade=None, age=None, affiliation=None, interests=[],
            interest_details=["티라노사우루스의 큰 이빨이 신기함"], growth_goal=None, end_intent="none",
            reaction="큰 이빨이 신기했구나!", question="너는 몇 학년이야?", asked_field="GRADE_OR_AGE",
        ),
    ]  # fmt: skip
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return outputs[len(calls) - 1]

    monkeypatch.setattr(ai_gate, "call_structured", fake)
    user = make_user(is_tester=True)
    session_id = begin(client, user)["sessionId"]
    first = reply(client, user, session_id, "나는 별이라고 불러 줘. 2학년이고 공룡을 좋아해.").json()
    assert first["assistantMessage"]["content"] == "별이는 공룡을 좋아하는구나! 어떤 공룡이 제일 좋고, 왜 좋아?"
    assert first["profileDraft"]["schoolOrGroup"] == "초등학교"
    assert "NICKNAME(불러 줄 별명)" in calls[0]["user_input"] and calls[0]["purpose"] == "first_greeting.extract"
    second = reply(client, user, session_id, "티라노사우루스 이빨이 커서 신기해").json()
    # AI 가 이미 아는 학년을 다시 물으면 버리고 다음 부족한 항목(GROWTH_GOAL)을 규칙 질문으로 묻는다.
    assert second["assistantMessage"]["content"].startswith("큰 이빨이 신기했구나! 마지막 질문!")
    assert second["readiness"]["missing"] == ["GROWTH_GOAL"]
    with next(db.get_session()) as session:
        confidences = {f.field: f.confidence for f in session.scalars(select(ConversationFact))}
        assert confidences["INTEREST_DETAIL"] == 0.8


def test_rule_parsers_keep_kid_phrases():
    from app.v1 import greeting_engine as ge
    from app.v1 import story_engine as se

    assert ge.parse_nickname("안녕!") is None and ge.parse_nickname("응") is None
    assert ge.parse_nickname("하늘이라고 불러줘") == "하늘"
    assert ge.parse_interests("그림 그리기랑 강아지 좋아해", asked=True) == ["그림 그리기", "강아지"]
    assert ge.parse_interests("나는 공룡이랑 축구를 좋아해", asked=True) == ["공룡", "축구"]
    assert ge.parse_interests("고양이", asked=True) == ["고양이"]
    assert ge.clean_goal("과학을 더 잘하고 싶어") == "과학 잘하기"
    assert ge.clean_goal("몰라") is None
    assert se.question_for("idea", {"hook": "눈이 오는 날을 본 적 있니? 눈으로 어떤 걸 할 수 있을까?"})[0] == (
        "눈으로 어떤 걸 할 수 있을까?"
    )
    assert se.reaction_for("idea", "눈사람을 만들 수 있을 것 같아.", None) == "“눈사람을 만들 수 있을 것 같아”라고 생각했구나."
