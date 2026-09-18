"""음성 조각 → 자막. 엔진을 갈아 끼울 수 있게 인터페이스 뒤에 둔다(명세 20절).

기본 엔진은 `app/services/speech.py` 의 OpenAI 전사 모델을 재사용해 **메모리 버퍼를 조금씩 다시 전사**한다.
서버가 OpenAI Realtime WebSocket 을 중계하는 방식도 검토했지만, 배포(서버리스 폴백)와 테스트가 무거워지고
같은 결과를 더 단순하게 얻을 수 있어 버퍼 방식을 골랐다. 실시간성이 더 필요해지면 `make_engine` 만 바꿔 끼운다.

지키는 것
- 음성은 메모리 버퍼에서만 다룬다. 파일로 남기지 않는다.
- 음성 원문·중간 자막·접속 ticket 을 로그에 남기지 않는다(호출 기록은 모델·지연시간만 남는다).
- AI 를 못 쓰면(키 없음·ZDR 게이트) `EngineUnavailable` 로 알리고, 호출하는 쪽이 오류 이벤트를 보낸다.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from typing import Protocol

from ..auth import ai_block_reason
from ..models import Child
from ..services import speech

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # s16le
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
# 중간 자막은 약 2초마다 한 번만 만든다(전사 호출 비용과 화면 반응 사이의 타협).
PARTIAL_EVERY_BYTES = BYTES_PER_SECOND * 2
MIN_FINAL_BYTES = BYTES_PER_SECOND // 5  # 0.2초 미만이면 말한 것으로 보지 않는다
# 전사 모델이 신뢰도를 주지 않는다. 화면 표시용 고정값이며 품질 판단에 쓰지 않는다.
DEFAULT_CONFIDENCE = 0.9


@dataclass
class FinalTranscript:
    text: str
    confidence: float
    duration_ms: int


class EngineUnavailable(Exception):
    """AI 전사를 지금 쓸 수 없다. code 는 사유(no_api_key·child_data_mode_off 등)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TranscriptionEngine(Protocol):
    """모두 동기 함수다. 호출하는 쪽이 스레드풀에서 돌린다."""

    def feed(self, chunk: bytes) -> None: ...

    def partial(self) -> str | None:
        """새 중간 자막이 있으면 전체 문장, 아직이면 None."""

    def final(self) -> FinalTranscript: ...

    def close(self) -> None: ...


def duration_ms(byte_count: int) -> int:
    return round(byte_count * 1000 / BYTES_PER_SECOND)


def pcm_to_wav(pcm: bytes) -> bytes:
    """16kHz mono s16le 원시 PCM 을 메모리에서 WAV 로 감싼다(전사 API 가 컨테이너를 요구한다)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return buffer.getvalue()


def wav_duration_ms(data: bytes) -> int | None:
    """업로드된 파일이 WAV 면 길이를 읽는다. 아니면 None(디코더를 두지 않는다)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as src:
            return round(src.getnframes() * 1000 / (src.getframerate() or SAMPLE_RATE))
    except (wave.Error, EOFError, ValueError):
        return None


class OpenAiBufferEngine:
    """받은 조각을 이어 붙여 두었다가 주기적으로 전사한다. 버퍼는 연결이 끝나면 버린다."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._since_partial = 0
        self._last_text = ""

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        self._since_partial += len(chunk)

    def partial(self) -> str | None:
        if self._since_partial < PARTIAL_EVERY_BYTES:
            return None
        self._since_partial = 0
        text = self._transcribe()
        if text == self._last_text:
            return None
        self._last_text = text
        return text

    def final(self) -> FinalTranscript:
        text = self._transcribe() if len(self._buffer) >= MIN_FINAL_BYTES else ""
        return FinalTranscript(text=text, confidence=DEFAULT_CONFIDENCE, duration_ms=duration_ms(len(self._buffer)))

    def close(self) -> None:
        self._buffer = bytearray()
        self._last_text = ""

    def _transcribe(self) -> str:
        if not self._buffer:
            return ""
        return speech.transcribe(pcm_to_wav(bytes(self._buffer)), "stream.wav", "audio/wav")


def make_engine(child: Child) -> TranscriptionEngine:
    """아이 음성을 AI 로 보내도 되는지 확인한 뒤 엔진을 만든다. 테스트는 이 함수를 바꿔 끼운다."""
    reason = ai_block_reason(child)
    if reason:
        raise EngineUnavailable(reason)
    return OpenAiBufferEngine()


def stable_prefix(previous: str, current: str) -> str:
    """두 중간 자막이 함께 가진 앞부분 — 화면에서 흔들리지 않는 구간."""
    limit = min(len(previous), len(current))
    end = 0
    while end < limit and previous[end] == current[end]:
        end += 1
    return current[:end]
