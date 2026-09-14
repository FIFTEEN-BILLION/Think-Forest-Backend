"""아이별 하루 AI 호출 한도(메모리). 재시작하면 초기화된다 — 운영에서는 DB·Redis 로 옮긴다."""

from __future__ import annotations

from .. import clock
from ..config import get_settings

_counts: dict[tuple[str, str], int] = {}


def try_consume(child_id: str) -> bool:
    key = (child_id, clock.kst(clock.now()).date().isoformat())
    if _counts.get(key, 0) >= get_settings().daily_ai_call_limit:
        return False
    _counts[key] = _counts.get(key, 0) + 1
    return True


def reset() -> None:
    _counts.clear()
