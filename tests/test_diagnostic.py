"""POST /diagnostic/assess — 진단 결과 폴백 동작."""

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _post(answers):
    return client.post("/diagnostic/assess", json={"answers": answers, "child": {"ageBand": "7-9"}})


def test_all_skipped_is_conservative():
    r = _post([{"prompt": "가장 좋아하는 놀이는?", "answer": ""} for _ in range(3)])
    assert r.status_code == 200
    body = r.json()
    assert body["ai"] is False
    assert body["followupIntensity"] == "gentle"
    assert body["vocabLevel"] == "easy"
    assert body["rationale"]


def test_rich_answers_raise_intensity():
    r = _post(
        [
            {
                "prompt": "왜 하늘은 파랄까?",
                "answer": "빛이 공기 알갱이에 부딪혀서 흩어지기 때문이에요. 파란 빛이 더 많이 흩어져서 그렇게 보여요.",
            },
            {
                "prompt": "가장 궁금한 것은?",
                "answer": "공룡이 왜 사라졌는지 궁금해요. 운석 때문이라는 얘기도 있고 화산 때문이라는 얘기도 있어서 비교해 보고 싶어요.",
            },
            {
                "prompt": "친구랑 다투면?",
                "answer": "먼저 왜 화가 났는지 말하고, 그 다음에 친구 얘기도 들어요. 그래야 서로 오해가 풀리니까요.",
            },
        ]
    )
    body = r.json()
    assert body["followupIntensity"] == "deep"
    assert body["vocabLevel"] in ("rich", "normal")


def test_response_schema_keys():
    r = _post([{"prompt": "무엇을 만들고 싶어?", "answer": "로봇이요"}])
    body = r.json()
    assert set(("ai", "followupIntensity", "vocabLevel", "rationale")).issubset(body)
