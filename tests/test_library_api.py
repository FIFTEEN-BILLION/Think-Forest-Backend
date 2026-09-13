"""단어 보관함·퀴즈·보호자 단어 검사·이야기책·공유·음성 API."""

from __future__ import annotations

from app.routers import speech as speech_router
from app.services import speech as speech_service

from conftest import grant, make_child, make_family, talk_to_story

WORDS = [
    {"word": "수증기", "meaning": "물이 눈에 안 보이는 기체가 된 것"},
    {"word": "겉면", "meaning": "물건의 바깥쪽 면"},
    {"word": "활주로", "meaning": "비행기가 달리다가 뜨고 내리는 긴 길"},
]


def test_word_bank_explain_and_quiz_without_scores(client, family, child):
    saved = [client.post("/children/me/words", json=w, headers=child["headers"]) for w in WORDS]
    assert [r.status_code for r in saved] == [201, 201, 201]
    again = client.post("/children/me/words", json=WORDS[0], headers=child["headers"])
    assert again.status_code == 200 and again.json()["id"] == saved[0].json()["id"]

    explained = client.post("/words/explain", json={"text": "수증기가 컵 겉면에 붙어요"}, headers=child["headers"]).json()
    assert explained["ai"] is False and {w["word"] for w in explained["words"]} == {"수증기", "겉면"}

    quiz = client.post("/children/me/word-quizzes", json={"count": 2}, headers=child["headers"]).json()
    assert len(quiz["questions"]) == 2 and all(len(q["options"]) == 3 for q in quiz["questions"])
    assert "answer" not in str(quiz["questions"])
    meanings = {w["word"]: w["meaning"] for w in WORDS}
    for q in quiz["questions"]:
        word = q["prompt"].split("’")[0].lstrip("‘")
        chosen = q["options"].index(meanings[word])
        result = client.post(
            f"/children/me/word-quizzes/{quiz['id']}/answers", json={"index": q["index"], "chosen": chosen},
            headers=child["headers"],
        ).json()  # fmt: skip
        assert result["correct"] is True
    duplicate = client.post(
        f"/children/me/word-quizzes/{quiz['id']}/answers", json={"index": 0, "chosen": 0}, headers=child["headers"]
    )
    assert duplicate.status_code == 409

    test = client.post(f"/guardian/children/{child['id']}/word-tests", json={"count": 3}, headers=family["headers"]).json()
    pending = client.get("/children/me/word-quizzes?pending=true", headers=child["headers"]).json()
    assert [p["id"] for p in pending] == [test["id"]] and pending[0]["assignedBy"] == "guardian"
    client.post(
        f"/children/me/word-quizzes/{test['id']}/answers", json={"index": 0, "chosen": 0}, headers=child["headers"]
    )
    results = client.get(f"/guardian/children/{child['id']}/word-tests", headers=family["headers"]).json()
    assert len(results[0]["answers"]) == 1


def test_quiz_needs_saved_words(client, child):
    assert client.post("/children/me/word-quizzes", json={}, headers=child["headers"]).status_code == 409


def test_books_collect_own_stories(client, child, frozen):
    story = talk_to_story(client, child, frozen)["final"]["story"]
    book = client.post(
        "/children/me/books", json={"title": "나의 과학 이야기책", "storyIds": [story["id"]]}, headers=child["headers"]
    ).json()
    assert book["stories"][0]["id"] == story["id"]
    other = make_child(client, make_family(client))
    r = client.post("/children/me/books", json={"title": "훔친 책", "storyIds": [story["id"]]}, headers=other["headers"])
    assert r.status_code == 404


def test_sharing_needs_permission_guardian_approval_and_hides_after_reports(client, family, child, frozen):
    story = talk_to_story(client, child, frozen)["final"]["story"]
    request = {"kind": "story", "refId": story["id"], "visibility": "family"}
    assert client.post("/children/me/shares", json=request, headers=child["headers"]).status_code == 403
    grant(client, child, publishRequest=True, browseShared=True)

    item = client.post("/children/me/shares", json=request, headers=child["headers"]).json()
    assert item["status"] == "pending_guardian" and item["authorLabel"] == "하늘"
    assert client.get("/shares", headers=child["headers"]).json() == []
    approved = client.post(
        f"/guardian/shares/{item['id']}/decision", json={"approve": True}, headers=family["headers"]
    ).json()
    assert approved["status"] == "published"
    assert [s["id"] for s in client.get("/shares", headers=child["headers"]).json()] == [item["id"]]

    outsider = make_family(client)
    assert client.get(f"/shares/{item['id']}", headers=outsider["headers"]).status_code == 404

    community = client.post(
        "/children/me/shares", json={**request, "visibility": "community"}, headers=child["headers"]
    ).json()
    decided = client.post(
        f"/guardian/shares/{community['id']}/decision", json={"approve": True}, headers=family["headers"]
    ).json()
    assert decided["status"] == "pending_review"  # ZDR 전 아동 콘텐츠는 외부 검사로 보내지 않고 사람 검토 대기

    reporters = [family, *(make_family(client) for _ in range(2))]
    circle = client.post("/guardian/circles", json={"name": "모임"}, headers=family["headers"]).json()
    circle_item = client.post(
        "/guardian/adventures",
        json={"category": "math", "title": "피자 모험", "hook": "피자를 어떻게 나눌까?", "visibility": "circle", "circleId": circle["id"]},
        headers=family["headers"],
    ).json()
    for reporter in reporters[1:]:
        client.post("/guardian/circles/join", json={"code": circle["code"]}, headers=reporter["headers"])
    statuses = [
        client.post(f"/shares/{circle_item['id']}/reports", headers=r["headers"]).json()["status"] for r in reporters
    ]
    assert statuses == ["published", "published", "hidden"]


def test_unsafe_guardian_adventure_is_rejected(client, family):
    r = client.post(
        "/guardian/adventures",
        json={"category": "history", "title": "선거 이야기", "hook": "대통령은 누가 좋을까?", "visibility": "family"},
        headers=family["headers"],
    )
    assert r.status_code == 422


def test_speech_needs_voice_permission_and_zdr(client, child):
    files = {"file": ("speech.webm", b"voice", "audio/webm")}
    assert client.post("/speech/transcriptions", files=files, headers=child["headers"]).json()["detail"] == (
        "permission_required:voice"
    )
    grant(client, child, voice=True)
    r = client.post("/speech/transcriptions", files=files, headers=child["headers"])
    assert r.status_code == 403 and r.json()["detail"] == "no_api_key"
    r = client.post("/speech/realtime-sessions", headers=child["headers"])
    assert r.status_code == 403


def test_speech_transcribes_and_issues_realtime_secret(client, child, monkeypatch):
    grant(client, child, voice=True)
    monkeypatch.setattr(speech_router, "ai_block_reason", lambda c: None)
    monkeypatch.setattr(speech_service, "transcribe", lambda data, name, mime: "빛이 높으면 그림자가 짧아져")
    monkeypatch.setattr(
        speech_service, "create_realtime_secret", lambda: {"value": "ek_test", "expires_at": 1, "model": "m"}
    )
    ok = client.post(
        "/speech/transcriptions", files={"file": ("a.webm", b"voice", "audio/webm;codecs=opus")}, headers=child["headers"]
    )
    assert ok.status_code == 200 and ok.json() == {"text": "빛이 높으면 그림자가 짧아져"}
    bad = client.post("/speech/transcriptions", files={"file": ("a.txt", b"x", "text/plain")}, headers=child["headers"])
    assert bad.status_code == 415
    monkeypatch.setattr(speech_service, "MAX_AUDIO_BYTES", 3)
    big = client.post("/speech/transcriptions", files={"file": ("a.wav", b"12345", "audio/wav")}, headers=child["headers"])
    assert big.status_code == 413
    secret = client.post("/speech/realtime-sessions", headers=child["headers"]).json()
    assert secret["clientSecret"] == "ek_test"
