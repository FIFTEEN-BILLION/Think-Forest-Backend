"""생각 친구 대화 API — 가족·권한, 대화 흐름, 안전, AI 검증, 이야기·마무리, 성장 기록, 상담."""

from __future__ import annotations

from app.auth import ai_block_reason
from app.config import get_settings
from app.models import Child
from app.routers import talks as talks_router
from app.schemas.talk import LlmWord, PlotLLM, PlotSceneLLM, TalkTurnLLM

from conftest import COMPOSE, auth, grant, make_child, make_family, talk_to_story, tick

# --- 가족 · 토큰 · 권한 ------------------------------------------------------


def test_guardian_and_child_tokens_are_scoped(client, family, child):
    other = make_family(client)
    assert client.get(f"/guardian/children/{child['id']}", headers=other["headers"]).status_code == 404
    assert client.get("/guardian/children", headers=child["headers"]).status_code == 401
    assert client.get("/me", headers=family["headers"]).status_code == 401
    me = client.get("/me", headers=child["headers"]).json()
    assert me["nickname"] == "하늘"
    assert me["permissions"] == {"voice": False, "browseShared": False, "publishRequest": False}
    grant(client, child, voice=True)
    assert client.get("/me", headers=child["headers"]).json()["permissions"]["voice"] is True
    client.delete(f"/guardian/children/{child['id']}/devices", headers=family["headers"])
    assert client.get("/me", headers=child["headers"]).status_code == 401


def test_real_child_data_needs_zdr_mode_but_tester_accounts_do_not(monkeypatch):
    settings = get_settings().model_copy(update={"openai_api_key": "sk-test", "child_data_mode": "demo"})
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    assert ai_block_reason(Child(is_tester=False)) == "child_data_mode_off"
    assert ai_block_reason(Child(is_tester=True)) is None
    monkeypatch.setattr("app.auth.get_settings", lambda: settings.model_copy(update={"child_data_mode": "child"}))
    assert ai_block_reason(Child(is_tester=False)) is None


# --- 대화 시작 · 흐름 ---------------------------------------------------------


def test_today_uses_weekday_theme_and_talk_opens_with_topic(client, child, frozen):
    today = client.get("/talks/today", headers=child["headers"]).json()
    assert today["theme"]["category"] == "science" and today["theme"]["weekdayLabel"] == "월"
    assert len(today["suggestions"]) == 3
    talk = client.post("/talks", json={}, headers=child["headers"]).json()
    first = talk["turns"][0]
    assert talk["topic"]["category"] == "science"
    assert first["role"] == "friend" and first["move"] == "hook"
    assert first["text"].startswith("안녕, 하늘! 오늘은 과학 탐험의 날이야.")
    assert talk["time"] == {"activeSeconds": 0, "minSeconds": 900, "remainingSeconds": 900, "canFinish": False}


def test_short_answers_get_a_sentence_prompt_and_do_not_advance(client, child, frozen):
    talk = client.post("/talks", json={"topicId": "airplane"}, headers=child["headers"]).json()
    body = client.post(f"/talks/{talk['id']}/turns", json={"text": "본적이 있어"}, headers=child["headers"]).json()
    assert body["accepted"] is False
    assert body["move"] == "hook"
    assert "문장" in body["friendTurn"]["text"]
    assert body["friendTurn"]["question"].startswith("하늘에 날아다니는 비행기")
    assert body["sentenceStarters"]
    body = client.post(
        f"/talks/{talk['id']}/turns", json={"text": "공항에서 커다란 비행기가 뜨는 걸 본 적이 있어"}, headers=child["headers"]
    ).json()
    assert body["accepted"] is True and body["move"] == "tail"


def test_sensitive_input_is_redirected_not_stored_and_reported(client, family, child, frozen):
    talk = client.post("/talks", json={"topicId": "snow"}, headers=child["headers"]).json()
    body = client.post(f"/talks/{talk['id']}/turns", json={"text": "나 어떻게 생겼는지 알아?"}, headers=child["headers"]).json()
    assert body["safety"] == "appearance" and body["accepted"] is False
    assert "생김새" not in body["childTurn"]["text"]
    client.post(f"/talks/{talk['id']}/turns", json={"text": "요즘 너무 죽고 싶어"}, headers=child["headers"])
    events = client.get(f"/guardian/children/{child['id']}/safety-events", headers=family["headers"]).json()
    assert {e["category"]: e["escalate"] for e in events} == {"appearance": False, "self_harm": True}


def test_personal_info_is_masked_before_storage(client, child, frozen):
    talk = client.post("/talks", json={"topicId": "ice_cup"}, headers=child["headers"]).json()
    body = client.post(
        f"/talks/{talk['id']}/turns",
        json={"text": "엄마 번호 010-1234-5678 로 물어봤는데 컵이 차가워서 물이 생긴대"},
        headers=child["headers"],
    ).json()
    assert body["accepted"] is True
    assert "010-1234-5678" not in body["childTurn"]["text"]


def test_rule_based_talk_reaches_story_and_finishes_after_15_minutes(client, family, child, frozen):
    run = talk_to_story(client, child, frozen)
    assert run["moves"] == ["tail", "connect", "challenge", "imagine", "tail", "reason_check", "tail", "compose"]
    final = run["final"]
    assert final["move"] == "continue"
    story = final["story"]
    assert story["source"] == "fallback" and len(story["scenes"]) >= 3
    assert story["scenes"][-1]["text"].startswith("처음에 나는")
    assert final["time"]["canFinish"] is True
    finished = client.post(f"/talks/{run['talk']['id']}/finish", headers=child["headers"]).json()
    assert finished["status"] == "completed"

    progress = client.get("/children/me/progress", headers=child["headers"]).json()
    counts = {a["id"]: a["count"] for a in progress["achievements"]}
    assert counts["compose"] == 1 and counts["finish"] == 1 and counts["imagination"] == 1
    assert counts["think_again"] == 1 and counts["reason_sentence"] >= 2
    assert progress["frequency"]["talksCompleted"] == 1 and progress["frequency"]["daysActive7"] == 1
    assert "score" not in str(progress).lower()
    review = client.get(f"/guardian/children/{child['id']}/talks/{run['talk']['id']}", headers=family["headers"])
    assert review.status_code == 200 and review.json()["story"]["id"] == story["id"]


def test_finish_needs_a_story_and_the_minimum_talk_time(client, child, frozen):
    talk = client.post("/talks", json={"topicId": "snow"}, headers=child["headers"]).json()
    r = client.post(f"/talks/{talk['id']}/finish", headers=child["headers"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "compose_first"

    run = talk_to_story(client, child, frozen, gap=10)
    assert run["final"]["story"] is not None
    r = client.post(f"/talks/{run['talk']['id']}/finish", headers=child["headers"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "min_time"
    for _ in range(6):  # 원하면 이야기를 이어 가며 시간을 채운다
        tick(frozen, 170)
        body = client.post(
            f"/talks/{run['talk']['id']}/turns", json={"text": COMPOSE}, headers=child["headers"]
        ).json()
        assert body["accepted"] is True
    finished = client.post(f"/talks/{run['talk']['id']}/finish", headers=child["headers"]).json()
    assert finished["status"] == "completed"
    assert finished["story"]["id"] != run["final"]["story"]["id"]  # 더 나눈 생각까지 담아 다시 만든다


# --- AI 출력 검증 --------------------------------------------------------------


def _allow_ai(monkeypatch):
    monkeypatch.setattr(talks_router, "ai_block_reason", lambda child: None)


def test_ai_turn_output_is_checked_and_cannot_change_the_flow(client, child, frozen, monkeypatch):
    _allow_ai(monkeypatch)
    outputs = [
        TalkTurnLLM(
            reaction="멋진 생각이야!", question="그런데 너는 어떻게 생겼어?", visual="question",
            hard_words=[], child_idea="", reason_given=False, new_idea=False, stance="none",
        ),
        TalkTurnLLM(
            reaction="“빈 컵”이라니 좋은 생각이야.", question="활주로 옆 컵에도 물방울이 생길까?", visual="water_drop",
            hard_words=[
                LlmWord(word="활주로", meaning="비행기가 달리는 길", example="비행기가 활주로를 달려요."),
                LlmWord(word="응결", meaning="없던 낱말", example=""),
            ],
            child_idea="컵이 차가우면 물이 생긴다", reason_given=True, new_idea=True, stance="none",
        ),
    ]  # fmt: skip
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return outputs[len(calls) - 1]

    monkeypatch.setattr(talks_router, "call_structured", fake)
    talk = client.post("/talks", json={"topicId": "ice_cup"}, headers=child["headers"]).json()
    first = client.post(
        f"/talks/{talk['id']}/turns", json={"text": "컵이 차가워서 물이 생긴 것 같아요."}, headers=child["headers"]
    ).json()
    assert first["ai"] is False and first["error"] == "output_replaced"
    assert "생겼어" not in first["friendTurn"]["text"]
    assert first["move"] == "tail"
    assert "tail" in calls[0]["user_input"] or "꼬리질문" in calls[0]["user_input"]
    second = client.post(
        f"/talks/{talk['id']}/turns", json={"text": "왜냐하면 캔 음료수에도 물방울이 생겼기 때문이야."}, headers=child["headers"]
    ).json()
    assert second["ai"] is True and second["move"] == "connect"
    assert [w["word"] for w in second["friendTurn"]["words"]] == ["활주로"]
    assert second["friendTurn"]["source"] == "ai"


def test_ai_plot_with_invented_turns_falls_back_to_child_sentences(client, child, frozen, monkeypatch):
    _allow_ai(monkeypatch)

    def fake(**kwargs):
        if kwargs["schema"] is PlotLLM:
            return PlotLLM(
                title="지어낸 이야기",
                scenes=[PlotSceneLLM(heading="가짜", text="아이가 말하지 않은 장면", from_turn_ids=["x"], visual="star")] * 3,
                ending_question="?",
            )
        raise talks_router.LlmError("bad_json", "x")

    monkeypatch.setattr(talks_router, "call_structured", fake)
    run = talk_to_story(client, child, frozen)
    story = run["final"]["story"]
    assert story["source"] == "fallback" and story["title"] != "지어낸 이야기"
    assert all(scene["fromTurnIds"] for scene in story["scenes"])


# --- 카테고리 · 공유 모험 ------------------------------------------------------


def test_custom_category_topics_and_talk(client, child, frozen):
    assert client.post("/children/me/categories", json={"name": "대통령"}, headers=child["headers"]).status_code == 422
    category = client.post("/children/me/categories", json={"name": "공룡"}, headers=child["headers"]).json()
    assert client.post("/children/me/categories", json={"name": "공룡"}, headers=child["headers"]).status_code == 409
    ideas = client.post(f"/children/me/categories/{category['id']}/topics", headers=child["headers"]).json()
    assert ideas["ai"] is False and len(ideas["topics"]) == 3
    talk = client.post(
        "/talks",
        json={"customCategoryId": category["id"], "customTopic": {"title": ideas["topics"][0]["title"], "hook": ideas["topics"][0]["hook"]}},
        headers=child["headers"],
    ).json()
    assert talk["category"] == "custom" and "공룡" in talk["turns"][0]["text"]


def test_guardian_adventure_can_open_a_talk_for_other_families(client, family, child, frozen):
    circle = client.post("/guardian/circles", json={"name": "우리반 친구들"}, headers=family["headers"]).json()
    friend_family = make_family(client)
    client.post("/guardian/circles/join", json={"code": circle["code"]}, headers=friend_family["headers"])
    friend = make_child(client, friend_family, nickname="바다")
    adventure = client.post(
        "/guardian/adventures",
        json={
            "category": "science", "title": "그림자 놀이", "hook": "손으로 어떤 그림자를 만들 수 있을까?",
            "followUps": ["그림자를 어디에 쓸 수 있을까?"], "visibility": "circle", "circleId": circle["id"],
        },
        headers=family["headers"],
    ).json()  # fmt: skip
    assert adventure["status"] == "published"
    blocked = client.post("/talks", json={"sharedItemId": adventure["id"]}, headers=friend["headers"])
    assert blocked.status_code == 403
    grant(client, friend, browseShared=True)
    talk = client.post("/talks", json={"sharedItemId": adventure["id"]}, headers=friend["headers"]).json()
    assert talk["topic"]["title"] == "그림자 놀이" and "손으로 어떤 그림자" in talk["turns"][0]["text"]


# --- 첫 만남 대화 --------------------------------------------------------------


def test_onboarding_chat_extracts_profile_without_school_name(client, family):
    child = make_child(client, family)
    start = client.get("/onboarding", headers=child["headers"]).json()
    assert "AI 생각 친구" in start["reply"] and start["missing"] == ["nickname", "school", "likes", "want_to_learn"]
    steps = ["내 별명은 하늘이야", "나는 햇살초등학교 3학년이야", "나는 공룡이랑 축구를 좋아해", "과학이랑 역사를 더 알고 싶어"]
    states = [client.post("/onboarding/messages", json={"text": s}, headers=child["headers"]).json() for s in steps]
    assert states[1]["notes"] == ["school_name_not_saved"]
    final = states[-1]
    assert final["done"] is True
    assert final["profile"] == {
        "nickname": "하늘", "grade": 3, "affiliation": "elementary", "likes": ["공룡", "축구"],
        "wantToLearn": ["과학", "역사"],
    }  # fmt: skip
    assert "햇살" not in str(client.get(f"/guardian/children/{child['id']}", headers=family["headers"]).json())
    confirmed = client.post("/onboarding/confirm", json={}, headers=child["headers"]).json()
    assert confirmed["profileConfirmed"] is True


def test_onboarding_redirects_sensitive_topics(client, family):
    child = make_child(client, family)
    state = client.post("/onboarding/messages", json={"text": "대통령 선거 얘기 하자"}, headers=child["headers"]).json()
    assert state["error"] == "redirected" and state["profile"]["nickname"] is None


# --- 상담(1달 뒤) ---------------------------------------------------------------


def test_guardian_consultation_opens_after_30_days(client, family, child, frozen):
    talk_to_story(client, child, frozen)
    r = client.post(f"/guardian/children/{child['id']}/consultations", headers=family["headers"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_yet"
    tick(frozen, 31 * 86400)
    r = client.post(f"/guardian/children/{child['id']}/consultations", headers=family["headers"])
    assert r.status_code == 201
    summary = r.json()["summary"]
    assert summary["highlights"] and summary["conversationTips"]


def test_token_header_format(client):
    assert client.get("/me", headers=auth("gt_wrong")).status_code == 401
    assert client.get("/me").status_code == 401
