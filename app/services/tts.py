"""텍스트 → 음성. Typecast 로만 보낸다.

합성한 오디오는 저장하지 않고 응답으로 그대로 흘려보낸다.
voice_id 는 고정값(설정에서 주입) — 요청에서 임의로 바꿀 수 없다.
"""

from __future__ import annotations

import time

import httpx

from ..config import get_settings
from . import diagnostics

TYPECAST_URL = "https://api.typecast.ai/v1/text-to-speech"
MAX_TEXT_CHARS = 2000


class TtsError(Exception):
    """Typecast 호출 실패. code 는 프론트에 그대로 노출 가능한 짧은 식별자."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def synthesize(text: str) -> tuple[bytes, str]:
    """text 를 오디오로 변환한다. (오디오 바이트, content-type) 을 돌려준다."""
    settings = get_settings()
    model = settings.typecast_model

    if not settings.typecast_enabled:
        _record(model, 0, False, "no_api_key")
        raise TtsError("no_api_key")

    started = time.perf_counter()
    try:
        resp = httpx.post(
            TYPECAST_URL,
            headers={"X-API-KEY": settings.typecast_api_key},
            json={
                "voice_id": settings.typecast_voice_id,
                "text": text,
                "model": model,
                "output": {"format": "mp3"},
            },
            timeout=30.0,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — 외부 API 예외가 다양해 코드로 감싸 올린다
        _record(model, round((time.perf_counter() - started) * 1000), False, type(exc).__name__)
        raise TtsError(type(exc).__name__) from exc

    _record(model, round((time.perf_counter() - started) * 1000), True, "ok")
    return resp.content, resp.headers.get("content-type", "audio/mpeg")


def _record(model: str, latency_ms: int, ok: bool, code: str) -> None:
    diagnostics.record_call(purpose="voice.synthesize", model=model, latency_ms=latency_ms, ok=ok, code=code)
