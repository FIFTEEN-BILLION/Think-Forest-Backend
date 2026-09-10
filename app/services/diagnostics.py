"""기술·안전 패널이 읽는 인메모리 로그.

프로세스 재시작하면 사라진다. 심사·데모용 진단이지 영속 저장소가 아니다.
개인식별정보나 API 키는 절대 여기 담지 않는다.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

_MAX = 100

_calls: deque[dict] = deque(maxlen=_MAX)
_blocks: deque[dict] = deque(maxlen=_MAX)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_call(*, purpose: str, model: str, latency_ms: int, ok: bool, code: str) -> None:
    _calls.append(
        {
            "purpose": purpose,
            "model": model,
            "latency_ms": latency_ms,
            "ok": ok,
            "code": code,
            "at": _now(),
        }
    )


def record_block(*, stage: str, surface: str, reason: str, term: str | None = None) -> None:
    _blocks.append(
        {
            "stage": stage,
            "surface": surface,
            "term": term,
            "reason": reason,
            "at": _now(),
        }
    )


def calls() -> list[dict]:
    return list(_calls)


def blocks() -> list[dict]:
    return list(_blocks)


def avg_latency_ms() -> int:
    done = [c["latency_ms"] for c in _calls if c["ok"]]
    return round(sum(done) / len(done)) if done else 0
