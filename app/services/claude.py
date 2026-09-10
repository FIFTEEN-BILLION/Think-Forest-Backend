"""Anthropic 호출 래퍼 — 유일한 통로.

라우터는 SDK를 직접 부르지 않는다. 이 함수만 부른다.
- 목적·모델·지연시간·성공/실패 코드를 로깅한다(기술 패널 노출).
- 실패는 삼키지 않고 ClaudeError 로 올린다. 코드가 응답에 실린다.
- 폴백은 라우터 책임. 여기서는 만들지 않는다.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ..config import get_settings
from ..prompts import theater as theater_prompt
from ..schemas.common import ChildContext
from ..schemas.theater import ScriptResponse
from . import diagnostics

# 마음극장 대본 실패 시 아이·부모에게 보여줄 안내. 절대 템플릿 대본을 지어내지 않는다.
_SCRIPT_UNAVAILABLE = "지금은 대본을 만들 수 없어요. 잠시 후 다시 시도해 주세요."


class ClaudeError(Exception):
    """AI 호출 실패. code 는 프론트에 그대로 노출 가능한 짧은 식별자."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _extract_json(text: str) -> Any:
    """모델 응답에서 JSON 객체만 뽑는다. 코드펜스·잡담을 견딘다."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def call_json(*, purpose: str, system: str, user: str, max_tokens: int = 1200) -> dict:
    """Claude 를 부르고 JSON dict 를 돌려준다. 실패 시 ClaudeError.

    purpose 예: "rubric.score", "theater.script".
    """
    settings = get_settings()
    model = settings.anthropic_model

    if not settings.ai_enabled:
        diagnostics.record_call(
            purpose=purpose, model=model, latency_ms=0, ok=False, code="no_api_key"
        )
        raise ClaudeError("no_api_key", "ANTHROPIC_API_KEY 가 설정되지 않았습니다")

    # SDK 는 여기서만 import — 키 없는 환경에서도 앱은 뜬다.
    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    started = time.perf_counter()
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        raw = "".join(block.text for block in resp.content if block.type == "text")
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("최상위가 객체가 아님")
    except ClaudeError:
        raise
    except json.JSONDecodeError as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
        diagnostics.record_call(
            purpose=purpose, model=model, latency_ms=latency_ms, ok=False, code="bad_json"
        )
        raise ClaudeError("bad_json", f"응답 JSON 파싱 실패: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — SDK 예외 다양, 코드로 감싸 올린다
        latency_ms = round((time.perf_counter() - started) * 1000)
        code = type(exc).__name__
        diagnostics.record_call(
            purpose=purpose, model=model, latency_ms=latency_ms, ok=False, code=code
        )
        raise ClaudeError(code, f"Claude 호출 실패: {exc}") from exc

    diagnostics.record_call(
        purpose=purpose, model=model, latency_ms=latency_ms, ok=True, code="ok"
    )
    return data


async def generate_script(*, keyword: str, child: ChildContext) -> ScriptResponse:
    """마음극장 대본 생성 (2차 AI 안전 심사 겸용). AsyncAnthropic 사용.

    실패해도 예외를 삼키지 않는다 — 목적·소요 ms·성공 여부를 로깅하고,
    사유를 담아 ai=False 로 반환한다. 템플릿 대본은 만들지 않는다.
    이 함수는 1차 금칙어 필터를 통과한 뒤에만 호출되어야 한다.
    """
    settings = get_settings()
    model = settings.anthropic_model
    purpose = "theater.script"

    if not settings.ai_enabled:
        diagnostics.record_call(
            purpose=purpose, model=model, latency_ms=0, ok=False, code="no_api_key"
        )
        return ScriptResponse(
            ai=False, error="no_api_key", safe=False, reason=_SCRIPT_UNAVAILABLE
        )

    # SDK 는 여기서만 import — 키 없는 환경에서도 앱은 뜬다.
    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    started = time.perf_counter()
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=1600,
            system=theater_prompt.SYSTEM,
            messages=[{"role": "user", "content": theater_prompt.build_user(keyword, child)}],
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
        raw = "".join(block.text for block in resp.content if block.type == "text")
        data = _extract_json(raw)
        if not isinstance(data, dict):
            raise ValueError("최상위가 객체가 아님")
    except Exception as exc:  # noqa: BLE001 — SDK/파싱 예외 다양. 삼키지 않고 사유를 반환.
        latency_ms = round((time.perf_counter() - started) * 1000)
        code = type(exc).__name__
        diagnostics.record_call(
            purpose=purpose, model=model, latency_ms=latency_ms, ok=False, code=code
        )
        return ScriptResponse(
            ai=False, error=code, safe=False, reason=_SCRIPT_UNAVAILABLE
        )

    # 2차 — 생성 단계 AI 심사. safe:false 면 대본을 폐기하고 사유만 돌려준다.
    if not data.get("safe", False):
        reason = data.get("reason") or "AI 안전 심사에서 부적절 판정"
        diagnostics.record_call(
            purpose=purpose, model=model, latency_ms=latency_ms, ok=True, code="unsafe"
        )
        diagnostics.record_block(stage="ai_review", surface="theater", reason=reason)
        return ScriptResponse(ai=True, safe=False, reason=reason)

    try:
        result = ScriptResponse(ai=True, **data)
    except Exception as exc:  # noqa: BLE001 — 스키마 불일치 시에도 지어내지 않는다.
        diagnostics.record_call(
            purpose=purpose, model=model, latency_ms=latency_ms, ok=False, code="bad_schema"
        )
        return ScriptResponse(
            ai=False,
            error=type(exc).__name__,
            safe=False,
            reason="대본 형식이 올바르지 않아 폐기했어요. 다시 시도해 주세요.",
        )

    diagnostics.record_call(
        purpose=purpose, model=model, latency_ms=latency_ms, ok=True, code="ok"
    )
    return result
