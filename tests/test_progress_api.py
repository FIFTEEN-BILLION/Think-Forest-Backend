"""성장 기록(점수 없는 빈도·성취 기준)과 1달 뒤 보호자 상담."""

from __future__ import annotations

from conftest import talk_to_story, tick


def test_progress_counts_frequency_and_achievements_without_scores(client, family, child, frozen):
    run = talk_to_story(client, child, frozen)
    client.post(f"/talks/{run['talk']['id']}/finish", headers=child["headers"])
    progress = client.get("/children/me/progress", headers=child["headers"]).json()
    counts = {a["id"]: a["count"] for a in progress["achievements"]}
    assert counts["compose"] == 1 and counts["finish"] == 1 and counts["imagination"] == 1
    assert counts["think_again"] == 1 and counts["reason_sentence"] >= 2
    assert progress["frequency"]["talksCompleted"] == 1 and progress["frequency"]["daysActive7"] == 1
    assert progress["stories"] == 1
    assert "score" not in str(progress).lower()
    guardian_view = client.get(f"/guardian/children/{child['id']}/progress", headers=family["headers"]).json()
    assert guardian_view == progress


def test_guardian_consultation_opens_after_30_days(client, family, child, frozen):
    talk_to_story(client, child, frozen)
    r = client.post(f"/guardian/children/{child['id']}/consultations", headers=family["headers"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_yet"
    tick(frozen, 31 * 86400)
    r = client.post(f"/guardian/children/{child['id']}/consultations", headers=family["headers"])
    assert r.status_code == 201
    summary = r.json()["summary"]
    assert summary["highlights"] and summary["conversationTips"]
    history = client.get(f"/guardian/children/{child['id']}/consultations", headers=family["headers"]).json()
    assert len(history) == 1
