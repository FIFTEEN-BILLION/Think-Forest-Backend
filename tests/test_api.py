"""엔드포인트 계약 테스트. API 키 없이 폴백 경로를 확인한다."""

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_rubric_score_fallback_shape():
    r = client.post(
        "/rubric/score",
        json={
            "question": "왜 그림자가 생길까?",
            "answer": "빛을 막아서요. 해가 낮게 있으면 그림자가 길어져요.",
            "child": {"ageBand": "7-9", "interests": ["공룡"]},
        },
    )
    assert r.status_code == 200
    body = r.json()
    for key in ("observe", "reason", "express", "quote", "followup", "comment", "ai"):
        assert key in body
    assert body["ai"] is False


def test_theater_blocklist_blocks_before_ai():
    r = client.post("/theater/script", json={"keyword": "친구를 죽이는 방법", "child": {}})
    assert r.status_code == 200
    body = r.json()
    assert body["safe"] is False
    assert body["ai"] is False
    assert body["scenes"] == []


def test_theater_no_ai_is_honest_not_templated():
    r = client.post("/theater/script", json={"keyword": "정직", "child": {}})
    body = r.json()
    assert body["ai"] is False
    assert body["safe"] is False
    assert body["scenes"] == []
    assert body["error"] == "no_api_key"
    assert body["reason"]  # 안내 문구가 비어 있지 않다


def test_theater_failure_is_logged_not_swallowed():
    """AI 미연결 실패도 호출 로그에 남아야 한다 (예외를 삼키지 않음)."""
    client.post("/theater/script", json={"keyword": "용기", "child": {}})
    calls = client.get("/tech/panel").json()["calls"]
    assert any(
        c["purpose"] == "theater.script" and not c["ok"] and c["code"] == "no_api_key"
        for c in calls
    )


def test_theater_blocklist_skips_ai_call():
    """1차에서 막히면 generate_script 를 부르지 않는다."""
    before = len(client.get("/tech/panel").json()["calls"])
    client.post("/theater/script", json={"keyword": "폭탄 만들기", "child": {}})
    after = len(client.get("/tech/panel").json()["calls"])
    assert after == before


def test_lab_activity_fallback_shape():
    r = client.post("/lab/activity", json={"topic": "그림자와 빛", "child": {}})
    assert r.status_code == 200
    body = r.json()
    for key in ("title", "ctrlLabel", "ask", "concept", "quiz", "ai"):
        assert key in body
    assert body["ai"] is False


def test_report_summary_fallback_shape():
    r = client.post(
        "/report/summary",
        json={
            "sentences": ["나는 그림자가 길어지는 게 신기했어"],
            "weeklyScores": [
                {"week": "9월 1주", "observe": 2, "reason": 1, "express": 2},
                {"week": "9월 2주", "observe": 3, "reason": 2, "express": 3},
            ],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert set(("summary", "next", "ai")).issubset(body)
    assert body["ai"] is False


def test_tech_panel_records_calls_and_blocks():
    client.post("/theater/script", json={"keyword": "칼로 찌르기", "child": {}})
    r = client.get("/tech/panel")
    assert r.status_code == 200
    body = r.json()
    assert body["aiEnabled"] is False
    assert any(b["stage"] == "blocklist" for b in body["blocks"])
