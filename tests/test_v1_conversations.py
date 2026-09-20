"""v1 티키와 이야기 — 시작·객관식/주관식 답·차원 누적·준비 판정·완료 정리본·멱등·취소·목록·복원·안전·소유권."""

from __future__ import annotations

import itertools
import re

from app import db
from app.models import SafetyEvent
from app.schemas.talk import PlotLLM, PlotSceneLLM
from app.v1 import ai_gate
from app.v1.accounts import create_account, issue_access_token
from app.v1.models_conversation import ConversationMessage, StoryRecord
from app.v1.schemas_conversation import ChoiceLLM, StoryTurnLLM
from sqlalchemy import func, select

from conftest import auth, tick

BASE = "/api/v1/conversations"
_ids = itertools.count(1)

IDEA = "컵이 차가워서 공기 속 물이 붙은 것 같아."
REASON = "왜냐하면 차가운 캔에도 물방울이 생겼기 때문이야."
ALTERNATIVE = "빈 컵이면 물이 샐 수 없으니까 물방울은 공기에서 온 거야."
REFLECTION = "처음에는 컵에서 샌다고 생각했는데 지금은 공기 속 물이라고 생각해."


def make_user(**kwargs) -> dict:
    session = next(db.get_session())
    user = create_account(session, **kwargs)
    raw = issue_access_token(session, user)
    session.commit()
    return {"id": user.id, "child_id": user.child_id, "headers": auth(raw)}


def say(client, user: dict, conversation: dict, text: str | None = None, option: str | None = None, **extra):
    """지금 질문(currentInteraction)에 답한다. conversation 은 {"id", "questionId"} 를 들고 다닌다."""
    body = {"clientMessageId": f"cm-{next(_ids)}", "questionId": conversation["questionId"], **extra}
    body["input"] = {"type": "SINGLE_CHOICE", "optionId": option} if option else {"type": "TEXT", "text": text}
    res = client.post(f"{BASE}/{conversation['id']}/messages", json=body, headers=user["headers"])
    if res.status_code == 200 and res.json()["nextInteraction"]:
        conversation["questionId"] = res.json()["nextInteraction"]["questionId"]
    return res


def start(client, user: dict, topic_id: str = "topic_ice_cup", key: str | None = None) -> dict:
    headers = {**user["headers"], **({"Idempotency-Key": key} if key else {})}
    res = client.post(BASE, json={"topicId": topic_id, "inputMode": "TEXT", "locale": "ko-KR"}, headers=headers)
    assert res.status_code == 201, res.text
    body = res.json()
    return {"id": body["conversationId"], "questionId": body["nextInteraction"]["questionId"], "start": body}


def talk_until_all_dimensions(client, user: dict, frozen: dict, conversation: dict, gap: int = 60) -> list[dict]:
    steps = [("option", "SEEN"), ("text", IDEA), ("text", REASON), ("text", ALTERNATIVE), ("option", "CHANGED")]
    steps.append(("text", REFLECTION))
    bodies = []
    for kind, value in steps:
        tick(frozen, gap)
        res = say(client, user, conversation, **({"option": value} if kind == "option" else {"text": value}))
        assert res.status_code == 200, res.text
        bodies.append(res.json())
    return bodies


def ready_story(client, user: dict, frozen: dict, topic_id: str = "topic_ice_cup") -> dict:
    conversation = start(client, user, topic_id)
    bodies = talk_until_all_dimensions(client, user, frozen, conversation)
    assert bodies[-1]["status"] == "READY_TO_FINISH"
    return conversation


# --- 시작 · 답 · 준비 판정 -----------------------------------------------------------


def test_fallback_conversation_accumulates_dimensions_to_ready_and_saves_story(client, frozen):
    user = make_user(nickname="별")
    conversation = start(client, user, key="start-1")
    first = conversation["start"]
    assert first["status"] == "ACTIVE"
    assert first["topic"] == {"id": "topic_ice_cup", "title": "얼음물 컵의 물방울", "category": "SCIENCE"}
    assert first["assistantMessage"]["content"].startswith("안녕, 별아! 오늘은 ‘얼음물 컵의 물방울’ 이야기를 해 보자.")
    assert first["nextInteraction"]["type"] == "SINGLE_CHOICE"
    assert [o["id"] for o in first["nextInteraction"]["options"]] == ["SEEN", "NOT_SEEN"]
    assert first["readiness"] == {
        "ready": False, "progress": 0, "coveredDimensions": [],
        "missingDimensions": ["EXPERIENCE", "IDEA", "REASON", "ALTERNATIVE", "REFLECTION"],
    }  # fmt: skip
    replay = client.post(BASE, json={"topicId": "topic_ice_cup"}, headers={**user["headers"], "Idempotency-Key": "start-1"})
    assert replay.status_code == 201 and replay.json() == first

    bodies = talk_until_all_dimensions(client, user, frozen, conversation)
    choice = bodies[0]
    assert choice["userMessage"]["content"] == "응, 있어"
    assert choice["userMessage"]["answer"] == {"type": "SINGLE_CHOICE", "optionId": "SEEN"}
    assert choice["readiness"]["coveredDimensions"] == ["EXPERIENCE"]
    assert choice["nextInteraction"]["type"] == "TEXT"
    assert choice["assistantMessage"]["source"] == "fallback"
    assert "어디에서 왔을까?" in choice["assistantMessage"]["content"]
    assert [b["readiness"]["coveredDimensions"][-1] for b in bodies[1:4]] == ["IDEA", "REASON", "ALTERNATIVE"]
    assert bodies[3]["nextInteraction"]["type"] == "SINGLE_CHOICE"  # 처음 생각 돌아보기
    assert "바뀌었는지" in bodies[4]["assistantMessage"]["content"]  # 고른 뒤 이유를 주관식으로 묻는다
    final = bodies[-1]
    assert final["status"] == "READY_TO_FINISH" and final["readiness"]["ready"] is True
    assert final["readiness"]["progress"] == 100 and final["readiness"]["missingDimensions"] == []
    assert "이제 이야기를 정리할 수 있어" in final["assistantMessage"]["content"]

    # READY_TO_FINISH 뒤에도 계속 대화할 수 있다.
    tick(frozen, 30)
    more = say(client, user, conversation, text="여름에 안경에 김이 서리는 것도 비슷한 일 같아.").json()
    assert more["status"] == "READY_TO_FINISH" and more["completion"] is None

    headers = {**user["headers"], "Idempotency-Key": "done-1"}
    done = client.post(f"{BASE}/{conversation['id']}/complete", json={"trigger": "BUTTON"}, headers=headers)
    assert done.status_code == 200, done.text
    story = done.json()["story"]
    assert done.json()["status"] == "COMPLETED"
    assert story["title"] == "별의 ‘얼음물 컵의 물방울’ 이야기"
    assert story["thoughtJourney"]["initialIdea"] == "컵이 차가워서 공기 속 물이 붙은 것 같아"
    assert story["thoughtJourney"]["evidence"][0].startswith("왜냐하면")
    assert story["thoughtJourney"]["alternatives"] == ["빈 컵이면 물이 샐 수 없으니까 물방울은 공기에서 온 거야"]
    assert story["thoughtJourney"]["finalReflection"] == "처음에는 컵에서 샌다고 생각했는데 지금은 공기 속 물이라고 생각해"
    assert "“컵이 차가워서 공기 속 물이 붙은 것 같아”" in story["body"]
    assert story["sourceConversationId"] == conversation["id"] and story["category"] == "SCIENCE"

    assert client.post(f"{BASE}/{conversation['id']}/complete", json={}, headers=headers).json() == done.json()
    again = client.post(f"{BASE}/{conversation['id']}/complete", headers=user["headers"])
    assert again.status_code == 200 and again.json()["story"]["id"] == story["id"]

    with next(db.get_session()) as session:
        record = session.get(StoryRecord, story["id"])
        assert record.ai_original is None and record.source == "fallback"
        assert record.thought_journey["initialIdea"] == story["thoughtJourney"]["initialIdea"]
    detail = client.get(f"{BASE}/{conversation['id']}", headers=user["headers"]).json()
    assert detail["status"] == "COMPLETED" and detail["storyId"] == story["id"] and detail["currentInteraction"] is None
    closed = say(client, user, conversation, text="하나 더 말하고 싶어요 정말로요.")
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "SESSION_CLOSED"


def test_minimum_responses_and_time_gate_ready(client, frozen):
    user = make_user()
    conversation = start(client, user)
    bodies = talk_until_all_dimensions(client, user, frozen, conversation, gap=10)
    last = bodies[-1]
    assert last["readiness"]["missingDimensions"] == [] and last["status"] == "ACTIVE"
    r = client.post(f"{BASE}/{conversation['id']}/complete", json={"trigger": "BUTTON"}, headers=user["headers"])
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "CONVERSATION_NOT_READY"
    assert error["details"]["missingDimensions"] == [] and error["details"]["remainingSeconds"] == 240

    tick(frozen, 150)
    body = say(client, user, conversation, text="내가 개미라면 컵 옆에서 물방울이 생기는 게 보일 것 같아.").json()
    assert body["status"] == "ACTIVE"
    tick(frozen, 150)
    body = say(client, user, conversation, text="욕실 거울에도 물방울이 생기는 걸 봤어.").json()
    assert body["status"] == "READY_TO_FINISH"


def test_not_ready_complete_lists_missing_dimensions(client, frozen):
    user = make_user()
    conversation = start(client, user)
    say(client, user, conversation, option="NOT_SEEN")
    r = client.post(f"{BASE}/{conversation['id']}/complete", json={"trigger": "BUTTON"}, headers=user["headers"])
    assert r.status_code == 409
    assert r.json()["error"]["details"]["missingDimensions"] == ["IDEA", "REASON", "ALTERNATIVE", "REFLECTION"]


def test_question_mismatch_and_short_answer_retry(client, frozen):
    user = make_user()
    conversation = start(client, user)
    old_question = conversation["questionId"]
    bad_option = say(client, user, conversation, option="MAYBE")
    assert bad_option.status_code == 409 and bad_option.json()["error"]["code"] == "QUESTION_MISMATCH"
    say(client, user, conversation, option="SEEN")
    stale = client.post(
        f"{BASE}/{conversation['id']}/messages",
        json={"clientMessageId": "stale", "questionId": old_question, "input": {"type": "TEXT", "text": IDEA}},
        headers=user["headers"],
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "QUESTION_MISMATCH"
    not_choice = say(client, user, conversation, option="SEEN")  # 지금은 주관식 질문
    assert not_choice.status_code == 409

    before = conversation["questionId"]
    short = say(client, user, conversation, text="몰라")
    assert short.status_code == 200
    body = short.json()
    assert body["nextInteraction"]["questionId"] != before  # 도움 요청에는 새 힌트와 도움 선택지를 준다
    assert "힌트" in body["assistantMessage"]["content"]
    assert body["readiness"]["coveredDimensions"] == ["EXPERIENCE"]
    invalid = client.post(
        f"{BASE}/{conversation['id']}/messages",
        json={"clientMessageId": "x", "input": {"type": "TEXT", "text": "  "}},
        headers=user["headers"],
    )
    assert invalid.status_code == 400 and invalid.json()["error"]["code"] == "INVALID_INPUT"


def test_short_text_answer_to_choice_question_is_understood(client, frozen):
    user = make_user()
    conversation = start(client, user)
    body = say(client, user, conversation, text="응 봤어").json()
    assert body["userMessage"]["answer"] == {"type": "SINGLE_CHOICE", "optionId": "SEEN"}
    assert body["readiness"]["coveredDimensions"] == ["EXPERIENCE"]


# --- 종료 의도 ------------------------------------------------------------------


def test_end_intent_completes_ready_conversation_in_the_same_request(client, frozen):
    user = make_user(nickname="하늘")
    conversation = ready_story(client, user, frozen)
    tick(frozen, 20)
    body = say(client, user, conversation, text="이제 그만할래").json()
    assert body["endIntentDetected"] is True and body["status"] == "COMPLETED"
    assert body["nextInteraction"] is None
    assert body["completion"]["status"] == "COMPLETED" and body["completion"]["story"]["id"].startswith("sty_")
    listed = client.get("/api/v1/stories", headers=user["headers"]).json()
    assert [s["id"] for s in listed["items"]] == [body["completion"]["story"]["id"]]


def test_end_intent_before_ready_asks_one_more_and_unsure_is_confirmed(client, frozen):
    user = make_user()
    conversation = start(client, user)
    say(client, user, conversation, option="SEEN")
    body = say(client, user, conversation, text="그만할래").json()
    assert body["endIntentDetected"] is True and body["status"] == "ACTIVE" and body["completion"] is None
    assert body["assistantMessage"]["content"].startswith("조금만 더 이야기하면")
    assert "어디에서 왔을까?" in body["assistantMessage"]["content"]

    unsure = say(client, user, conversation, text="졸려").json()
    assert unsure["endIntentDetected"] is False
    assert [o["id"] for o in unsure["nextInteraction"]["options"]] == ["END", "CONTINUE"]
    resumed = say(client, user, conversation, option="CONTINUE").json()
    assert resumed["assistantMessage"]["content"].startswith("좋아, 계속 이야기하자!")
    assert resumed["nextInteraction"]["type"] == "TEXT" and resumed["status"] == "ACTIVE"


# --- 멱등 · 취소 · 목록 · 복원 ---------------------------------------------------


def test_client_message_id_replays_without_storing_twice(client, frozen):
    user = make_user()
    conversation = start(client, user)
    body = {"clientMessageId": "device-uuid-1", "input": {"type": "SINGLE_CHOICE", "optionId": "SEEN"}}
    first = client.post(f"{BASE}/{conversation['id']}/messages", json=body, headers=user["headers"])
    second = client.post(f"{BASE}/{conversation['id']}/messages", json=body, headers=user["headers"])
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    detail = client.get(f"{BASE}/{conversation['id']}", headers=user["headers"]).json()
    assert len(detail["messages"]) == 3  # 시작 질문 + 아이 답 + 티키 답


def test_cancel_list_filter_and_cursor(client, frozen):
    user = make_user()
    ids = []
    for topic in ("topic_ice_cup", "topic_snow", "topic_airplane"):
        tick(frozen, 5)
        ids.append(start(client, user, topic)["id"])
    tick(frozen, 5)
    cancelled = client.post(f"{BASE}/{ids[0]}/cancel", headers=user["headers"])
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert client.post(f"{BASE}/{ids[0]}/cancel", headers=user["headers"]).json()["status"] == "CANCELLED"
    closed = client.post(
        f"{BASE}/{ids[0]}/messages",
        json={"clientMessageId": "late", "input": {"type": "SINGLE_CHOICE", "optionId": "SEEN"}},
        headers=user["headers"],
    )
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "SESSION_CLOSED"
    not_ready = client.post(f"{BASE}/{ids[0]}/complete", headers=user["headers"])
    assert not_ready.status_code == 409 and not_ready.json()["error"]["code"] == "SESSION_CLOSED"

    page1 = client.get(f"{BASE}?status=ACTIVE,READY_TO_FINISH&limit=1", headers=user["headers"]).json()
    assert [i["conversationId"] for i in page1["items"]] == [ids[2]] and page1["nextCursor"]
    page2 = client.get(f"{BASE}?status=ACTIVE,READY_TO_FINISH&limit=1&cursor={page1['nextCursor']}", headers=user["headers"])
    assert [i["conversationId"] for i in page2.json()["items"]] == [ids[1]] and page2.json()["nextCursor"] is None
    everything = client.get(BASE, headers=user["headers"]).json()
    assert [i["conversationId"] for i in everything["items"]] == [ids[0], ids[2], ids[1]]
    assert client.get(f"{BASE}?status=CANCELLED", headers=user["headers"]).json()["items"][0]["status"] == "CANCELLED"
    assert client.get(f"{BASE}?status=DONE", headers=user["headers"]).status_code == 400
    assert client.get(f"{BASE}?cursor=not-a-cursor", headers=user["headers"]).status_code == 400
    assert client.get(f"{BASE}?limit=51", headers=user["headers"]).status_code == 400


def test_get_restores_messages_with_message_cursor(client, frozen):
    user = make_user()
    conversation = start(client, user)
    talk_until_all_dimensions(client, user, frozen, conversation, gap=10)  # 시작 1 + 답 6쌍 = 13개
    latest = client.get(f"{BASE}/{conversation['id']}?limit=5", headers=user["headers"]).json()
    assert len(latest["messages"]) == 5 and latest["nextCursor"] == latest["messages"][0]["id"]
    assert latest["currentInteraction"]["questionId"] == conversation["questionId"]
    assert latest["messages"][-1]["role"] == "ASSISTANT"
    older = client.get(
        f"{BASE}/{conversation['id']}?limit=50&messageCursor={latest['nextCursor']}", headers=user["headers"]
    ).json()
    assert len(older["messages"]) == 8 and older["nextCursor"] is None
    assert older["messages"][0]["content"].startswith("안녕!")
    assert older["messages"][0]["interaction"]["options"][0] == {"id": "SEEN", "label": "응, 있어"}  # 선택지 스냅숏
    assert client.get(f"{BASE}/{conversation['id']}?messageCursor=msg_nope", headers=user["headers"]).status_code == 400


# --- 안전 · 소유권 ---------------------------------------------------------------


def test_unsafe_content_is_rejected_and_not_stored(client, frozen):
    user = make_user()
    conversation = start(client, user)
    say(client, user, conversation, option="SEEN")
    r = say(client, user, conversation, text="요즘 너무 죽고 싶어")
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSAFE_CONTENT"
    assert "어른" in r.json()["error"]["message"]
    with next(db.get_session()) as session:
        stored = session.scalars(select(ConversationMessage.content)).all()
        assert not any("죽고" in c for c in stored)
        events = session.scalars(select(SafetyEvent).where(SafetyEvent.child_id == user["child_id"])).all()
        assert [(e.category, e.escalate) for e in events] == [("self_harm", True)]
    masked = say(client, user, conversation, text="엄마 번호 010-1234-5678 로 물어봤는데 컵이 차가워서 물이 생긴대").json()
    assert "010-1234-5678" not in masked["userMessage"]["content"]


def test_other_users_conversation_is_not_found(client, frozen):
    owner, other = make_user(), make_user()
    conversation = start(client, owner)
    for method, path, body in (
        ("get", f"{BASE}/{conversation['id']}", None),
        ("post", f"{BASE}/{conversation['id']}/messages", {"clientMessageId": "a", "input": {"type": "TEXT", "text": IDEA}}),
        ("post", f"{BASE}/{conversation['id']}/complete", {}),
        ("post", f"{BASE}/{conversation['id']}/cancel", None),
        ("get", "/api/v1/first-greeting/sessions/" + conversation["id"], None),
    ):
        res = client.request(method.upper(), path, json=body, headers=other["headers"])
        assert res.status_code == 404 and res.json()["error"]["code"] == "SESSION_NOT_FOUND", path
    assert client.get(BASE, headers=other["headers"]).json()["items"] == []
    assert client.post(BASE, json={"topicId": "topic_nope"}, headers=owner["headers"]).json()["error"]["code"] == (
        "TOPIC_NOT_FOUND"
    )
    assert client.get(BASE).status_code == 401


# --- AI 경로(모의) ----------------------------------------------------------------


def _turn(**overrides) -> StoryTurnLLM:
    base = dict(
        reaction="“공기 속 물”이라니 멋진 생각이야.", question="", options=[], child_idea="", shared_experience=False,
        reason_given=False, new_idea=False, stance="none", end_intent="none",
    )  # fmt: skip
    return StoryTurnLLM(**{**base, **overrides})


def test_ai_turns_offer_choices_and_plot_keeps_child_quotes(client, frozen, monkeypatch):
    monkeypatch.setattr(ai_gate, "ai_block_reason", lambda child: None)
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        if kwargs["schema"] is PlotLLM:
            ids = re.findall(r"id=(msg_[0-9a-f]+)", kwargs["user_input"])
            scene = [PlotSceneLLM(heading="처음 생각", text="별이는 컵 밖 물방울을 궁금해했어요.", from_turn_ids=[i], visual="water_drop") for i in ids[:3]]  # noqa: E501
            return PlotLLM(title="컵 밖의 작은 물방울", scenes=scene, ending_question="또 어디에서 물방울을 볼까?")
        move = re.search(r"\[이번 질문 종류\] (\S+ \S+)", kwargs["user_input"]).group(1)
        if move == "생각 묻기":  # 생각 묻기 — 고를 수 있게 선택지를 준다
            return _turn(
                question="그 물은 어디에서 왔을까?", shared_experience=True,
                options=[ChoiceLLM(id="air water", label="공기 속 물"), ChoiceLLM(id="LEAK", label="컵 안에서 샌 물")],
            )  # fmt: skip
        if move == "이유 묻기":
            return _turn(question="어떤 단서로 그렇게 생각했어?", options=[ChoiceLLM(id="A", label="아무거나"), ChoiceLLM(id="B", label="둘")])
        return _turn(question="너는 어떻게 생각해?", reason_given=True)

    monkeypatch.setattr(ai_gate, "call_structured", fake)
    user = make_user(nickname="별", is_tester=True)
    conversation = start(client, user)
    tick(frozen, 60)
    body = say(client, user, conversation, option="SEEN").json()
    assert body["nextInteraction"]["type"] == "SINGLE_CHOICE"
    assert body["nextInteraction"]["options"] == [{"id": "AIR_WATER", "label": "공기 속 물"}, {"id": "LEAK", "label": "컵 안에서 샌 물"}]  # noqa: E501
    assert body["assistantMessage"]["content"] == "“공기 속 물”이라니 멋진 생각이야. 그 물은 어디에서 왔을까?"
    assert body["assistantMessage"]["source"] == "ai" and body["userMessage"]["source"] is None
    tick(frozen, 60)
    body = say(client, user, conversation, option="AIR_WATER").json()
    assert body["userMessage"]["content"] == "공기 속 물" and "IDEA" in body["readiness"]["coveredDimensions"]
    assert body["nextInteraction"]["type"] == "TEXT"  # 이유를 묻는 질문에는 선택지를 주지 않는다
    for text in (REASON, ALTERNATIVE):
        tick(frozen, 60)
        assert say(client, user, conversation, text=text).status_code == 200
    tick(frozen, 60)
    say(client, user, conversation, option="KEPT")
    tick(frozen, 60)
    body = say(client, user, conversation, text="나는 처음 생각을 그대로 믿어. 캔에서도 봤기 때문이야.").json()
    assert body["status"] == "READY_TO_FINISH"

    story = client.post(f"{BASE}/{conversation['id']}/complete", json={}, headers=user["headers"]).json()["story"]
    assert story["title"] == "컵 밖의 작은 물방울"
    assert story["thoughtJourney"]["initialIdea"] == "공기 속 물"
    assert "별이는 컵 밖 물방울을 궁금해했어요." not in story["body"]  # 본문은 아이 말 인용 우선
    with next(db.get_session()) as session:
        record = session.get(StoryRecord, story["id"])
        assert record.source == "ai" and record.ai_original["title"] == "컵 밖의 작은 물방울"
        assert len(record.ai_original["scenes"]) == 3
        assert session.scalar(select(func.count(ConversationMessage.id))) == 13
    assert any(c["purpose"] == "conversation.plot" for c in calls)
