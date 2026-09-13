"""음성 → 문장. OpenAI 로만 보낸다(Azure·ffmpeg 없음).

- transcribe: 녹음이 끝난 짧은 파일(webm·mp4·m4a·wav·mp3·ogg) → 한국어 문장.
- create_realtime_secret: 말하는 동안 글자를 바로 보여 주는 실시간 인식용 임시 키.
  브라우저가 이 키로 OpenAI Realtime 에 직접 붙는다. 서버 API 키는 노출하지 않는다.
음성과 인식 문장은 저장하거나 로그에 남기지 않는다.
"""

from __future__ import annotations

import time
from typing import Any

from ..config import get_settings
from . import diagnostics, llm

MAX_AUDIO_BYTES = 5 * 1024 * 1024
ALLOWED_AUDIO_TYPES = frozenset(
    {"audio/webm", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/mpeg", "audio/wav", "audio/x-wav", "audio/ogg"}
)
REALTIME_SECRET_SECONDS = 120


class SpeechError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def base_mime(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _record(purpose: str, model: str, started: float, ok: bool, code: str) -> None:
    diagnostics.record_call(
        purpose=purpose, model=model, latency_ms=round((time.perf_counter() - started) * 1000), ok=ok, code=code
    )


def transcribe(data: bytes, filename: str, mime: str) -> str:
    settings = get_settings()
    model = settings.openai_transcribe_model
    started = time.perf_counter()
    try:
        resp = llm._get_client().audio.transcriptions.create(
            model=model, file=(filename, data, mime), languages=["ko"]
        )
    except Exception as exc:  # noqa: BLE001
        _record("speech.transcribe", model, started, False, type(exc).__name__)
        raise SpeechError(type(exc).__name__) from exc
    _record("speech.transcribe", model, started, True, "ok")
    return (getattr(resp, "text", "") or "").strip()


def create_realtime_secret() -> dict[str, Any]:
    """transcription 전용 세션의 임시 키. 세션 내부 language 필드 이름은 공식 문서로 재확인 필요."""
    settings = get_settings()
    model = settings.openai_realtime_model
    started = time.perf_counter()
    try:
        resp = llm._get_client().realtime.client_secrets.create(
            expires_after={"anchor": "created_at", "seconds": REALTIME_SECRET_SECONDS},
            session={
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {"model": model, "language": "ko"},
                        "turn_detection": {"type": "server_vad"},
                    }
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        _record("speech.realtime_secret", model, started, False, type(exc).__name__)
        raise SpeechError(type(exc).__name__) from exc
    _record("speech.realtime_secret", model, started, True, "ok")
    return {"value": resp.value, "expires_at": resp.expires_at, "model": model}
