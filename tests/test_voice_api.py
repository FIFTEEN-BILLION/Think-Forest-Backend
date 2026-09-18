"""음성 합성(TTS) API — 권한, 금칙어 1차 필터, 키 없을 때 실패 경로."""

from __future__ import annotations

from app.services import tts as tts_service

from conftest import grant


def test_synthesize_needs_voice_permission(client, child):
    grant(client, child, voice=False)
    r = client.post("/voice/synthesize", json={"text": "안녕"}, headers=child["headers"])
    assert r.status_code == 403 and r.json()["detail"] == "permission_required:voice"


def test_synthesize_blocks_forbidden_text_before_calling_typecast(client, child, monkeypatch):
    grant(client, child, voice=True)
    called = False

    def fail_if_called(text: str) -> tuple[bytes, str]:
        nonlocal called
        called = True
        return b"", "audio/mpeg"

    monkeypatch.setattr(tts_service, "synthesize", fail_if_called)
    r = client.post("/voice/synthesize", json={"text": "죽여버릴거야"}, headers=child["headers"])
    assert r.status_code == 403 and r.json()["detail"] == "blocked_content"
    assert called is False


def test_synthesize_without_api_key_fails_cleanly(client, child):
    grant(client, child, voice=True)
    r = client.post("/voice/synthesize", json={"text": "숲 속 이야기를 들려줄게"}, headers=child["headers"])
    assert r.status_code == 502 and r.json()["detail"] == "synthesis_failed:no_api_key"


def test_synthesize_returns_audio(client, child, monkeypatch):
    grant(client, child, voice=True)
    monkeypatch.setattr(tts_service, "synthesize", lambda text: (b"fake-audio-bytes", "audio/mpeg"))
    r = client.post("/voice/synthesize", json={"text": "숲 속 이야기를 들려줄게"}, headers=child["headers"])
    assert r.status_code == 200
    assert r.content == b"fake-audio-bytes"
    assert r.headers["content-type"] == "audio/mpeg"
