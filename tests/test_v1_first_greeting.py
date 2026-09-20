"""서버 AI 첫인사 계약 테스트. 모의 AI는 해석 정확도가 아니라 저장·출처·재시도 계약을 검증한다."""

from __future__ import annotations

import itertools
import json

import pytest
from app import db
from app.models import SafetyEvent
from app.safety import pii
from app.v1 import ai_gate
from app.v1.greeting_engine import empty_draft
from app.v1.models_conversation import ChildProfile, ConversationFact, ConversationMessage, ConversationSession
from app.v1.schemas_conversation import FirstGreetingLLM
from sqlalchemy import select

from test_v1_conversations import make_user

BASE = "/api/v1/first-greeting/sessions"
_ids = itertools.count(1)
ALIEN = "나는 외계 32행성에서 온 외계인 삐리빠라 3세야 나를 삐리빠라 3세에서 3세는 빼고 불러도 돼"
AMBIGUOUS = "나는 감자일까 아닐까? 너가 맞춰보세요 저는 숭실초 3학년이 아닙니다 제가 좋아하는것은 아직 말 안할거에용"


def output(message="이야기를 들려줄래?", changes=None, *, review=False, summary=None, memory="", end="none"):
    return dict(
        message=message,
        changes=changes or [],
        context_summary=memory,
        propose_review=review,
        profile_summary=summary,
        end_intent=end,
    )


def change(field, value=None, *, operation="SET", values=None, quote=None, message_id="CURRENT"):
    return dict(
        field=field,
        operation=operation,
        value=value,
        values=values or [],
        evidence=[dict(message_id=message_id, quote=quote or "CURRENT")],
    )


@pytest.fixture
def ai(monkeypatch):
    queue, calls = [], []
    monkeypatch.setattr(ai_gate, "ai_block_reason", lambda _: None)
    monkeypatch.setattr(ai_gate, "moderate", lambda *_: None)

    def fake(**kwargs):
        data = json.loads(kwargs["user_input"])
        calls.append(data)
        if data["event"] == "START":
            return FirstGreetingLLM(**output("안녕! 나는 티키야. 오늘은 어떤 이야기를 해 볼까?"))
        assert queue, "unscripted AI call"
        scripted = queue.pop(0)
        if isinstance(scripted, Exception):
            raise scripted
        scripted = json.loads(json.dumps(scripted))
        for item in scripted["changes"]:
            for evidence in item["evidence"]:
                if evidence["message_id"] == "CURRENT":
                    evidence["message_id"] = data["current_message_id"]
                if evidence["quote"] == "CURRENT":
                    evidence["quote"] = data["messages"][-1]["content"]
        return FirstGreetingLLM(**scripted)

    monkeypatch.setattr(ai_gate, "call_structured", fake)
    return queue, calls


def begin(client, user, key=None):
    r = client.post(BASE, headers={**user["headers"], **({"Idempotency-Key": key} if key else {})})
    assert r.status_code == 200, r.text
    return r.json()


def reply(client, user, sid, text=None, *, option=None, cmid=None, question=None):
    return client.post(
        f"{BASE}/{sid}/messages",
        headers=user["headers"],
        json={
            "clientMessageId": cmid or f"fg-{next(_ids)}",
            "questionId": question,
            "input": {"type": "SINGLE_CHOICE", "optionId": option} if option else {"type": "TEXT", "text": text},
        },
    )


def filled(client, user, sid, ai):
    ai[0].append(
        output(
            "별, 2학년, 공룡이 좋고 큰 이빨이 신기하구나. 질문하기를 배우고 싶다고 기억하면 될까?",
            [
                change("nickname", "별"),
                change("gradeOrAgeBand", "2학년"),
                change("interests", values=["공룡"]),
                change("interestDetails", values=["큰 이빨이 신기해"]),
                change("growthGoal", "질문하기"),
            ],
            review=True,
            summary="별은 공룡을 좋아하고 질문하기를 배우고 싶어 해요.",
        )
    )
    res = reply(
        client, user, sid, "별이라고 불러줘. 2학년이야. 공룡의 큰 이빨이 신기해서 좋아. 질문하기를 배우고 싶어."
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_ai_opening_resume_pagination_and_idempotency(client, frozen, ai):
    user = make_user()
    start = begin(client, user, key="start-once")
    assert start["messages"][0]["content"] == "안녕! 나는 티키야. 오늘은 어떤 이야기를 해 볼까?"
    assert start["messages"][0]["source"] == "ai"
    assert start["profileDraft"] == empty_draft()
    assert begin(client, user)["resumed"] is True
    assert begin(client, user, key="start-once") == start
    assert len(ai[1]) == 1
    ai[0].append(
        output(
            "반가워, 삐리빠라! 32행성에서는 어떤 놀이를 해?",
            [change("nickname", "삐리빠라")],
            memory="역할놀이 설정: 32행성 외계인",
        )
    )
    r = reply(client, user, start["sessionId"], ALIEN, cmid="one")
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["profileDraft"] == {**empty_draft(), "nickname": "삐리빠라"}
    assert result["assistantMessage"]["content"] == "반가워, 삐리빠라! 32행성에서는 어떤 놀이를 해?"
    assert result["assistantMessage"]["source"] == "ai"
    assert result["profileRevision"] == 1
    assert reply(client, user, start["sessionId"], "다른 말", cmid="one").json() == result
    assert len(ai[1]) == 2
    detail = client.get(f"{BASE}/{start['sessionId']}?limit=2", headers=user["headers"]).json()
    assert detail["profileDraft"] == result["profileDraft"] and detail["nextCursor"]
    assert len(detail["messages"]) == 2
    older = client.get(
        f"{BASE}/{start['sessionId']}", params={"messageCursor": detail["nextCursor"]}, headers=user["headers"]
    ).json()
    assert len(older["messages"]) == 1
    with next(db.get_session()) as session:
        facts = list(session.scalars(select(ConversationFact)))
        assert [(f.field, f.value) for f in facts] == [("NICKNAME", "삐리빠라")]
        assert facts[0].source_message_id == result["userMessage"]["id"]


def test_ai_controls_question_order_roleplay_and_deferral(client, frozen, ai):
    user = make_user()
    sid = begin(client, user)["sessionId"]
    ai[0].append(
        output(
            "아직은 비밀이구나. 그럼 오늘 어떤 이야기를 해 볼까?",
            [change("interests", operation="DEFER")],
            memory="관심사 공개를 원하지 않음",
        )
    )
    result = reply(client, user, sid, AMBIGUOUS).json()
    assert result["profileDraft"] == empty_draft()
    assert result["deferredFields"] == ["interests"]
    ai[0].append(output("행성의 풍경이 궁금해!", [], memory="역할놀이 설정: 행성 놀이"))
    next_turn = reply(client, user, sid, "내 행성에서는 운석을 던지는 놀이를 해").json()
    assert next_turn["profileDraft"] == empty_draft()
    assert next_turn["deferredFields"] == ["interests"]
    assert next_turn["assistantMessage"]["content"] == "행성의 풍경이 궁금해!"
    assert ai[1][-1]["deferred"] == ["interests"]
    assert ai[1][-1]["context_summary"] == "관심사 공개를 원하지 않음"
    assert any(m["content"] == AMBIGUOUS for m in ai[1][-1]["messages"] if m["role"] == "USER")


def test_semantic_corrections_and_defer_preserve_existing_values(client, frozen, ai):
    user = make_user()
    sid = begin(client, user)["sessionId"]
    filled(client, user, sid, ai)
    ai[0].append(output("지금 말하지 않아도 괜찮아.", [change("interests", operation="DEFER")]))
    deferred = reply(client, user, sid, "좋아하는 건 지금 이야기 안 할래").json()
    assert deferred["profileDraft"]["interests"] == ["공룡"]
    assert deferred["deferredFields"] == ["interests"]
    ai[0].append(
        output(
            "바로잡아 줘서 고마워. 축구는 어떤 점이 좋아?",
            [
                change("gradeOrAgeBand", "4학년"),
                change("interests", values=["공룡"], operation="REMOVE"),
                change("interests", values=["축구"], operation="ADD"),
                change("interestDetails", operation="CLEAR"),
            ],
        )
    )
    corrected = reply(client, user, sid, "2학년 아니라 4학년이야. 공룡 좋아한다는 건 취소하고 축구가 좋아.").json()
    assert corrected["profileDraft"]["gradeOrAgeBand"] == "4학년"
    assert corrected["profileDraft"]["interests"] == ["축구"]
    assert corrected["profileDraft"]["interestDetails"] == []
    assert corrected["deferredFields"] == []
    with next(db.get_session()) as session:
        assert (
            session.scalar(
                select(ConversationFact).where(
                    ConversationFact.session_id == sid,
                    ConversationFact.field == "INTEREST_DETAIL",
                    ConversationFact.current.is_(True),
                )
            )
            is None
        )


def test_completion_requires_current_explicit_review(client, frozen, ai):
    user = make_user()
    sid = begin(client, user)["sessionId"]
    early = client.post(f"{BASE}/{sid}/complete", headers=user["headers"], json={"profileRevision": 0})
    assert early.status_code == 409 and early.json()["error"]["code"] == "FIRST_GREETING_NOT_READY"
    review = filled(client, user, sid, ai)
    assert review["status"] == "READY_TO_FINISH"
    assert client.get("/api/v1/me", headers=user["headers"]).json()["user"]["needsFirstGreeting"]
    stale = client.post(f"{BASE}/{sid}/complete", headers=user["headers"], json={"profileRevision": 0})
    assert stale.status_code == 409
    assert reply(client, user, sid, option="CONFIRM_PROFILE").status_code == 409
    ai[0].append(output("수정할 이름을 알려줄래?"))
    editing = reply(client, user, sid, option="EDIT_NICKNAME", question=review["nextInteraction"]["questionId"]).json()
    assert editing["profileDraft"]["nickname"] == "별"
    assert (
        client.post(
            f"{BASE}/{sid}/complete", headers=user["headers"], json={"profileRevision": review["profileRevision"]}
        ).status_code
        == 409
    )
    ai[0].append(
        output(
            "그럼 달이라고 부를게. 이 정보가 맞으면 확인 버튼을 눌러 줘.",
            [change("nickname", "달")],
            review=True,
            summary="달은 공룡을 좋아해요.",
        )
    )
    corrected = reply(client, user, sid, "달이라고 불러줘").json()
    assert (
        reply(client, user, sid, option="CONFIRM_PROFILE", question=review["nextInteraction"]["questionId"]).status_code
        == 409
    )
    done = reply(
        client, user, sid, option="CONFIRM_PROFILE", question=corrected["nextInteraction"]["questionId"], cmid="confirm"
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "COMPLETED"
    assert done.json()["completion"]["profile"]["nickname"] == "달"
    assert done.json()["completion"]["summary"] == "달은 공룡을 좋아해요."
    assert done.json()["nextInteraction"] is None
    assert reply(client, user, sid, option="CONFIRM_PROFILE", cmid="confirm").json() == done.json()
    assert reply(client, user, sid, "또 이야기").status_code == 409
    assert not client.get("/api/v1/me", headers=user["headers"]).json()["user"]["needsFirstGreeting"]
    with next(db.get_session()) as session:
        assert session.scalar(select(ChildProfile)).nickname == "달"


def test_natural_language_end_does_not_implicitly_save_profile(client, frozen, ai):
    user = make_user()
    sid = begin(client, user)["sessionId"]
    review = filled(client, user, sid, ai)
    ai[0].append(
        output(
            "그럼 쉬었다 이야기하자. 저장하려면 내용을 확인하고 버튼을 눌러 줘.",
            review=True,
            summary="별은 공룡을 좋아해요.",
            end="clear",
        )
    )
    result = reply(client, user, sid, "이제 그만할래").json()
    assert result["endIntentDetected"] and result["completion"] is None
    done = client.post(
        f"{BASE}/{sid}/complete", headers=user["headers"], json={"profileRevision": review["profileRevision"]}
    )
    assert done.status_code == 200, done.text
    assert (
        client.post(
            f"{BASE}/{sid}/complete", headers=user["headers"], json={"profileRevision": review["profileRevision"]}
        ).json()
        == done.json()
    )


@pytest.mark.parametrize(
    "reason", ["no_api_key", "ai_disabled", "child_data_mode_off", "daily_limit", "session_call_limit"]
)
def test_ai_block_does_not_create_fake_dialogue_or_extract(client, frozen, ai, monkeypatch, reason):
    user = make_user()
    sid = begin(client, user)["sessionId"]
    monkeypatch.setattr(ai_gate, "budget", lambda *_: reason)
    result = reply(client, user, sid, "나는 별이고 3학년이고 공룡을 좋아해")
    assert result.status_code == 503
    assert result.json()["error"]["details"]["reason"] == reason
    restored = client.get(f"{BASE}/{sid}", headers=user["headers"]).json()
    assert restored["profileDraft"] == empty_draft() and len(restored["messages"]) == 1
    assert len(ai[1]) == 1


def test_failed_start_and_failed_turn_are_retryable_without_duplicates(client, frozen, ai, monkeypatch):
    user = make_user()
    with monkeypatch.context() as patch:
        patch.setattr(ai_gate, "budget", lambda *_: "ai_disabled")
        assert client.post(BASE, headers=user["headers"]).status_code == 503
    with next(db.get_session()) as session:
        assert session.scalar(select(ConversationSession)) is None
    sid = begin(client, user)["sessionId"]
    ai[0].append(ai_gate.LlmError("timeout", "test"))
    assert reply(client, user, sid, ALIEN, cmid="retry-me").status_code == 503
    ai[0].append(output("반가워!", [change("nickname", "삐리빠라")]))
    result = reply(client, user, sid, ALIEN, cmid="retry-me")
    assert result.status_code == 200
    detail = client.get(f"{BASE}/{sid}", headers=user["headers"]).json()
    assert len(detail["messages"]) == 3
    assert detail["profileDraft"]["nickname"] == "삐리빠라"


@pytest.mark.parametrize(
    "bad_change",
    [
        change("nickname", "없는이름", quote="원문에 없는 근거"),
        change("nickname", "별", message_id="another-session"),
        change("nickname", "별", operation="ADD"),
        change("nickname", "이름" * 15),
        change("schoolOrGroup", "햇살초등학교"),
        change("interests", values=["하나", "둘", "셋", "넷", "다섯", "여섯"]),
    ],
)
def test_invalid_ai_output_is_atomic_and_never_falls_back_to_rules(client, frozen, ai, bad_change):
    user = make_user()
    sid = begin(client, user)["sessionId"]
    ai[0].append(output("이 결과는 표시되면 안 돼.", [change("gradeOrAgeBand", "3학년"), bad_change]))
    res = reply(client, user, sid, "별이야 3학년이야")
    assert res.status_code == 503 and res.json()["error"]["details"]["reason"] == "invalid_ai_output"
    state = client.get(f"{BASE}/{sid}", headers=user["headers"]).json()
    assert state["profileDraft"] == empty_draft() and len(state["messages"]) == 1


def test_school_privacy_and_user_only_evidence(client, frozen, ai):
    assert pii.mask("햇살초등학교 3학년", names=False, preserve_school_types=True).text == "●●● 초등학교 3학년"
    assert pii.mask("초등학교", names=False, preserve_school_types=True).text == "초등학교"
    user = make_user()
    start = begin(client, user)
    ai[0].append(output("알려 줘서 고마워.", [change("schoolOrGroup", "초등학교"), change("gradeOrAgeBand", "3학년")]))
    result = reply(client, user, start["sessionId"], "햇살초등학교 3학년이야").json()
    assert result["profileDraft"]["schoolOrGroup"] == "초등학교"
    assert "햇살" not in json.dumps(ai[1][-1], ensure_ascii=False)
    ai[0].append(
        output(
            "안 되는 결과",
            [change("nickname", "티키", quote=start["messages"][0]["content"], message_id=start["messages"][0]["id"])],
        )
    )
    assert reply(client, user, start["sessionId"], "응").status_code == 503


def test_ownership_question_mismatch_and_input_safety(client, frozen, ai):
    owner, other = make_user(), make_user()
    sid = begin(client, owner)["sessionId"]
    assert client.get(f"{BASE}/{sid}", headers=other["headers"]).status_code == 404
    assert reply(client, other, sid, "안녕").status_code == 404
    assert reply(client, owner, sid, "안녕", question="q-old").status_code == 409
    assert reply(client, owner, sid, "대통령 선거 얘기 하자").status_code == 422
    assert client.post(BASE).status_code == 401
    with next(db.get_session()) as session:
        assert session.scalar(select(SafetyEvent)).category == "politics"
        assert len(list(session.scalars(select(ConversationMessage)))) == 1
