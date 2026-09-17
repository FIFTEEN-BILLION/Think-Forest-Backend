"""OpenAI Moderation 래퍼(무료). 금칙어·민감 주제 1차 필터 다음 단계다.

호출할 수 없으면 available=False 로 돌려준다. 호출하는 쪽이 안전한 기본값을 고른다
(대화는 1차 필터로 계속, 전체 공개는 사람 검토 대기).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..config import get_settings
from . import diagnostics, llm


@dataclass(frozen=True)
class ModerationResult:
    available: bool
    flagged: bool
    categories: tuple[str, ...] = ()


def moderate(text: str) -> ModerationResult:
    settings = get_settings()
    if not settings.openai_enabled or not (text or "").strip():
        return ModerationResult(False, False)
    started = time.perf_counter()
    try:
        resp = llm._get_client().moderations.create(model=settings.moderation_model, input=text)
        result = resp.results[0]
        flags = result.categories.model_dump(by_alias=True)
        categories = tuple(sorted(name for name, hit in flags.items() if hit))
        flagged = bool(result.flagged)
    except Exception as exc:  # noqa: BLE001 — 검사 실패는 '검사 못 함'으로 정직하게 돌려준다
        diagnostics.record_call(
            purpose="moderation",
            model=settings.moderation_model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            ok=False,
            code=type(exc).__name__,
        )
        return ModerationResult(False, False)
    diagnostics.record_call(
        purpose="moderation",
        model=settings.moderation_model,
        latency_ms=round((time.perf_counter() - started) * 1000),
        ok=True,
        code="flagged" if flagged else "ok",
    )
    return ModerationResult(True, flagged, categories)
