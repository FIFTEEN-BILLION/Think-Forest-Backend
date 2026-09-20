"""도움 요청은 문장 늘리기·학습 근거가 아니라 힌트와 재시도 경로로 처리한다."""

import pytest
from app import db
from app.talks.sentences import check_sentence
from app.v1 import ai_gate, story_engine
from app.v1.models_conversation import ConversationMessage, ConversationSession
from sqlalchemy import select

from test_v1_conversations import BASE, IDEA, make_user, say, start, talk_until_all_dimensions


@pytest.mark.parametrize("text", [
    "잘 모르겠어 ㅠㅠ", "나는 아직 잘 모르겠어", "몰라요...", "모르겠어요 ㅜㅜ",
    "왜냐하면 잘 모르기 때문이야", "무슨 말인지 모르겠어", "이해가 안 돼", "너무 어려워",
    "힌트 좀 줘", "설명해 주세요", "어떻게 해야 할지 모르겠어",
])
def test_uncertainty_is_help_even_when_it_is_a_long_sentence(text):
    result = check_sentence(text)
    assert not result.ok and result.reason == "unsure"


@pytest.mark.parametrize("text", [
    "잘 모르겠지만 컵이 차가워서 그런 것 같아.",
    "왜 그런지 모르겠지만 공기에서 온 것 같아.",
    "글쎄 나는 컵에서 물이 샌 것 같아.",
])
def test_uncertainty_with_an_actual_idea_is_still_accepted(text):
    assert check_sentence(text).ok


@pytest.mark.parametrize("move", ["idea", "reason", "imagine"])
def test_help_does_not_credit_reason_and_can_resume_original_question(client, frozen, monkeypatch, move):
    monkeypatch.setattr(ai_gate, "budget", lambda *_: "test_no_ai")
    user = make_user()
    conversation = start(client, user)
    if move == "imagine":
        talk_until_all_dimensions(client, user, frozen, conversation, gap=10)
    else:
        say(client, user, conversation, option="SEEN")
        if move == "reason":
            say(client, user, conversation, text=IDEA)
    with next(db.get_session()) as session:
        row = session.get(ConversationSession, conversation["id"])
        before, original = dict(row.readiness), dict(row.current_interaction)
        assert original["move"] == move

    # AI가 켜져 있어도 도움 요청은 문장 신호로 보내거나 근거로 계산하지 않는다.
    monkeypatch.setattr(ai_gate, "budget", lambda *_: None)
    monkeypatch.setattr(ai_gate, "call", lambda **_: pytest.fail("Help must not be evaluated as a learning answer"))
    response = say(client, user, conversation, text="잘 모르겠어 ㅠㅠ", clientMessageId="need-help")
    assert response.status_code == 200, response.text
    body = response.json()
    content = body["assistantMessage"]["content"]
    assert "수증기" in content and "모르는 건 괜찮아" in content
    assert "왜냐하면" not in content and "길게" not in content
    assert body["endIntentDetected"] is False
    assert [o["id"] for o in body["nextInteraction"]["options"]] == ["SUPPORT_HINT", "SUPPORT_RETRY"]

    # 재전송과 새로고침에도 같은 도움 단계를 복원한다.
    replay = client.post(f"{BASE}/{conversation['id']}/messages", headers=user["headers"], json={
        "clientMessageId": "need-help", "questionId": original["questionId"],
        "input": {"type": "TEXT", "text": "잘 모르겠어 ㅠㅠ"},
    })
    assert replay.json() == body
    restored = client.get(f"{BASE}/{conversation['id']}", headers=user["headers"]).json()
    assert restored["currentInteraction"] == body["nextInteraction"]
    more = say(client, user, conversation, option="SUPPORT_HINT").json()
    assert "물방울로 바뀌어요" in more["assistantMessage"]["content"]
    resumed = say(client, user, conversation, option="SUPPORT_RETRY").json()
    assert original["prompt"] in resumed["assistantMessage"]["content"]
    with next(db.get_session()) as session:
        row = session.get(ConversationSession, conversation["id"])
        assert row.readiness == before
        assert row.current_interaction["move"] == move
        messages = list(session.scalars(select(ConversationMessage).where(ConversationMessage.session_id == row.id)))
        help_messages = [m for m in messages if m.role == "USER"][-3:]
        assert all(not m.meta["valid"] and not m.meta["dims"] for m in help_messages)
        assert "모르겠어" not in str(story_engine.thought_journey(messages))

    monkeypatch.setattr(ai_gate, "budget", lambda *_: "test_no_ai")
    answer = say(client, user, conversation, text=IDEA)
    assert answer.status_code == 200
    assert "왜냐하면'을 붙여" not in answer.json()["assistantMessage"]["content"]


def test_topic_without_facts_supports_help_without_inventing_facts(client, frozen):
    user = make_user()
    conversation = start(client, user, "topic_today")
    response = say(client, user, conversation, text="무슨 말인지 모르겠어")
    assert response.status_code == 200
    body = response.json()
    assert "익숙한 물건이나 장면" in body["assistantMessage"]["content"]
    assert body["readiness"]["coveredDimensions"] == []
    again = say(client, user, conversation, option="SUPPORT_RETRY").json()
    assert again["nextInteraction"]["type"] == "SINGLE_CHOICE"
    assert [o["id"] for o in again["nextInteraction"]["options"]] == ["SEEN", "NOT_SEEN"]


def test_child_can_answer_directly_while_viewing_hints(client, frozen, monkeypatch):
    monkeypatch.setattr(ai_gate, "budget", lambda *_: "test_no_ai")
    user = make_user()
    conversation = start(client, user)
    say(client, user, conversation, option="SEEN")
    say(client, user, conversation, text="나는 아직 잘 모르겠어")
    answer = say(client, user, conversation, text="잘 모르겠지만 공기에서 온 것 같아.")
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["readiness"]["coveredDimensions"] == ["EXPERIENCE", "IDEA"]
    assert "왜 그렇게 생각했어" in body["assistantMessage"]["content"]
