"""사고력 엔진 API 계약·안전 테스트. OpenAI 호출은 mock 한다."""

import pytest
from app.main import app
from app.routers import inquiry as inquiry_router
from app.schemas.inquiry import ChallengeLLM, InterpretLLM, LlmClaim, TeachLLM
from app.services.llm import LlmError
from fastapi.testclient import TestClient

client = TestClient(app)
BASE = {"lightHeight": "mid", "stickHeight": "short", "distance": "near", "brightness": "dim"}


def card(**change):
    return {"base": BASE, "compare": {**BASE, **change}}


@pytest.fixture
def fake_llm(monkeypatch):
    calls: list[dict] = []

    def install(result):
        def fake(**kwargs):
            calls.append(kwargs)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(inquiry_router, "call_structured", fake)
        return calls

    return install


def interpret_out(**overrides):
    data = {
        "claims": [],
        "uncertain": False,
        "restatement": "빛이 높으면 그림자가 달라질 거라고 생각했구나.",
        "friend_belief_id": "brightness_longer",
        "friend_line": "나는 빛이 밝으면 길어질 것 같아. 어떻게 확인할까?",
    }
    return InterpretLLM(**{**data, **overrides})


def test_mission_is_single_source_and_demo_by_default():
    body = client.get("/missions/shadow").json()
    assert len(body["table"]) == 24
    assert body["childDataMode"] == "demo"
    assert {"designFeedback", "friendBeliefs", "facts", "truth"} <= body.keys()


def test_demo_mode_rejects_child_origin(fake_llm):
    calls = fake_llm(interpret_out())
    r = client.post("/inquiry/interpret", json={"prediction": "shorter", "reason": "음", "inputOrigin": "child"})
    assert r.status_code == 403
    assert calls == []


def test_interpret_falls_back_honestly(fake_llm):
    fake_llm(LlmError("no_api_key", "x"))
    body = client.post(
        "/inquiry/interpret", json={"prediction": "shorter", "reason": "해가 높으면 그림자가 짧았어"}
    ).json()
    assert body["ai"] is False and body["source"] == "fallback"
    assert body["error"] == "ai_failed:no_api_key"
    assert {"variable": "lightHeight", "effect": "shorter"} in body["claims"]
    assert body["friendBeliefId"] == "brightness_longer"


def test_pii_is_masked_before_llm(fake_llm):
    calls = fake_llm(interpret_out())
    body = client.post(
        "/inquiry/interpret",
        json={"prediction": "shorter", "reason": "내 번호는 010-1234-5678 인데 빛이 높으면 짧아"},
    ).json()
    assert body["ai"] is True
    assert "010-1234-5678" not in calls[0]["user_input"]


def test_blocked_input_never_reaches_llm(fake_llm):
    calls = fake_llm(interpret_out())
    body = client.post("/inquiry/interpret", json={"prediction": "longer", "reason": "친구를 죽이는 방법"}).json()
    assert calls == []
    assert body["error"] == "blocked"


def test_llm_cannot_give_friend_the_childs_own_idea(fake_llm):
    fake_llm(interpret_out(friend_belief_id="light_higher_longer"))
    body = client.post("/inquiry/interpret", json={"prediction": "longer", "reason": "그냥"}).json()
    assert body["friendBeliefId"] != "light_higher_longer"
    assert "belief_replaced" in body["error"]


def test_llm_prediction_chip_is_not_overwritten(fake_llm):
    fake_llm(interpret_out(claims=[LlmClaim(variable="lightHeight", effect="longer")]))
    body = client.post("/inquiry/interpret", json={"prediction": "shorter", "reason": "음"}).json()
    assert body["claims"][0] == {"variable": "lightHeight", "effect": "shorter"}


def test_friend_line_leaking_the_rule_is_replaced(fake_llm):
    fake_llm(interpret_out(friend_line="빛을 높이면 그림자가 짧아지잖아! 밝기는 어때?"))
    body = client.post("/inquiry/interpret", json={"prediction": "unknown", "reasonSkipped": True}).json()
    assert "짧아지" not in body["friendLine"]
    assert "line_replaced" in body["error"]


def teach_out(**overrides):
    data = {
        "claim": LlmClaim(variable="brightness", effect="same"),
        "uses_evidence": True,
        "missing": "none",
        "probe_reply": "어떤 카드에서 봤어?",
        "convinced_reply": "네 카드 보니까 알겠어. 생각 바꿀게!",
    }
    return TeachLLM(**{**data, **overrides})


def test_teach_without_cards_never_convinces_even_if_llm_is_sure(fake_llm):
    fake_llm(teach_out())
    body = client.post(
        "/inquiry/teach", json={"beliefId": "brightness_longer", "message": "밝아도 같아", "attempt": 1}
    ).json()
    assert body["convinced"] is False
    assert body["missing"] == "evidence"
    assert body["helpLevel"] == "probe"
    assert body["friendReply"] != "네 카드 보니까 알겠어. 생각 바꿀게!"


def test_teach_with_fair_card_convinces(fake_llm):
    fake_llm(teach_out())
    body = client.post(
        "/inquiry/teach",
        json={"beliefId": "brightness_longer", "message": "밝게만 했는데 똑같았어", "cards": [card(brightness="bright")], "attempt": 1},
    ).json()
    assert body["convinced"] is True
    assert body["friendReply"] == "네 카드 보니까 알겠어. 생각 바꿀게!"


def test_teach_unfair_card_escalates_help(fake_llm):
    fake_llm(teach_out())
    body = client.post(
        "/inquiry/teach",
        json={
            "beliefId": "brightness_longer",
            "message": "밝게 했더니 짧아졌어",
            "cards": [card(brightness="bright", lightHeight="high")],
            "attempt": 2,
        },
    ).json()
    assert body["convinced"] is False
    assert body["missing"] == "fairness"
    assert body["helpLevel"] == "hint"


def test_teach_rule_fallback_and_call_limit(fake_llm):
    calls = fake_llm(LlmError("no_api_key", "x"))
    payload = {"beliefId": "brightness_longer", "message": "밝게 해도 그림자 길이가 똑같았어", "cards": [card(brightness="bright")]}
    body = client.post("/inquiry/teach", json={**payload, "attempt": 1}).json()
    assert body["ai"] is False and body["convinced"] is True
    body = client.post("/inquiry/teach", json={**payload, "attempt": 4}).json()
    assert body["error"] == "call_limit"
    assert len(calls) == 1


def test_challenge_uses_bank_and_server_lengths(fake_llm):
    fake_llm(ChallengeLLM(final_claims=[LlmClaim(variable="lightHeight", effect="shorter")], challenge_id="far_light"))
    body = client.post(
        "/inquiry/challenge",
        json={"beliefId": "brightness_longer", "convinced": True, "experiments": [card(lightHeight="high")], "finalText": "높으면 짧아"},
    ).json()
    assert body["challenge"]["id"] == "far_light"
    assert body["challenge"]["compareLength"] > body["challenge"]["baseLength"]
    assert body["challenge"]["friendCorrect"] is False


def test_challenge_fallback_after_convincing_is_confound(fake_llm):
    fake_llm(LlmError("no_api_key", "x"))
    body = client.post("/inquiry/challenge", json={"beliefId": "brightness_longer", "convinced": True}).json()
    assert body["ai"] is False
    assert body["challenge"]["id"] == "confounded_claim"
    assert body["challenge"]["confounded"] is True
