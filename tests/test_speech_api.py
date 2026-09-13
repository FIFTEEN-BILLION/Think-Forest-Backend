"""음성 입력 API — 보호자 음성 권한과 ZDR 조건, 파일 형식·크기, 실시간 인식 키."""

from __future__ import annotations

from app.routers import speech as speech_router
from app.services import speech as speech_service

from conftest import grant


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
