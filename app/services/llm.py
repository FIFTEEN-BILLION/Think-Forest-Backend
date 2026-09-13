"""OpenAI 호출 래퍼 — 첫 탐구 사고력 엔진의 유일한 통로.

라우터는 SDK를 직접 부르지 않는다. 이 함수만 부른다.
- Responses API + 구조화 출력(Pydantic 스키마 강제), store=False.
- 목적·모델·지연시간·성공/실패 코드를 로깅한다(아이 문장은 남기지 않는다).
- 실패는 삼키지 않고 LlmError 로 올린다. 폴백은 라우터 책임.
"""

from __future__ import annotations

import time
from typing import Any, TypeVar

from pydantic import BaseModel

from ..config import get_settings
from . import diagnostics

T = TypeVar("T", bound=BaseModel)

_client: Any = None


class LlmError(Exception):
    """AI 호출 실패. code 는 프론트에 그대로 노출 가능한 짧은 식별자."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _get_client() -> Any:
    global _client
    if _client is None:
        # SDK 는 여기서만 import — 키 없는 환경에서도 앱은 뜬다.
        from openai import OpenAI

        settings = get_settings()
        _client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_s,
            max_retries=1,
        )
    return _client


def _has_refusal(resp: Any) -> bool:
    for item in getattr(resp, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", "") == "refusal":
                return True
    return False


def call_structured(
    *,
    purpose: str,
    instructions: str,
    user_input: str,
    schema: type[T],
    max_output_tokens: int = 2000,
) -> T:
    """스키마를 강제한 응답을 돌려준다. 실패 시 LlmError."""
    settings = get_settings()
    model = settings.openai_model

    if not settings.openai_enabled:
        code = "ai_disabled" if settings.openai_api_key else "no_api_key"
        diagnostics.record_call(purpose=purpose, model=model, latency_ms=0, ok=False, code=code)
        raise LlmError(code, "사고력 엔진 AI 호출이 꺼져 있습니다")

    extra: dict[str, Any] = {}
    if settings.openai_reasoning_effort:
        extra["reasoning"] = {"effort": settings.openai_reasoning_effort}

    started = time.perf_counter()
    try:
        resp = _get_client().responses.parse(
            model=model,
            instructions=instructions,
            input=user_input,
            text_format=schema,
            store=False,
            max_output_tokens=max_output_tokens,
            **extra,
        )
    except Exception as exc:  # noqa: BLE001 — SDK·검증 예외 다양, 코드로 감싸 올린다
        latency_ms = round((time.perf_counter() - started) * 1000)
        code = type(exc).__name__
        diagnostics.record_call(purpose=purpose, model=model, latency_ms=latency_ms, ok=False, code=code)
        raise LlmError(code, f"OpenAI 호출 실패: {code}") from exc

    latency_ms = round((time.perf_counter() - started) * 1000)
    parsed = resp.output_parsed
    if parsed is None:
        if _has_refusal(resp):
            code = "refusal"
        elif getattr(resp, "status", "") == "incomplete":
            code = "incomplete"
        else:
            code = "empty"
        diagnostics.record_call(purpose=purpose, model=model, latency_ms=latency_ms, ok=False, code=code)
        raise LlmError(code, f"구조화 응답 없음: {code}")

    diagnostics.record_call(purpose=purpose, model=model, latency_ms=latency_ms, ok=True, code="ok")
    return parsed
