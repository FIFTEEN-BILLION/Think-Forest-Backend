"""아이 발화 저장 — 인메모리(프로토타입).

규칙:
- 보호자 동의(consent.guardian)가 없으면 저장하지 않는다. 체험은 막지 않는다.
- 저장 직전 safety/pii.py 로 마스킹. 원문은 절대 남기지 않는다.
- 보관기간 기본 90일. 접근할 때마다 만료분을 삭제한다.
실제 DB 는 이후 작업. 인터페이스만 먼저 고정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import get_settings
from ..safety import pii


@dataclass
class StoredUtterance:
    text: str  # 이미 마스킹된 텍스트
    masked_categories: list[str]
    at: datetime


@dataclass
class Store:
    items: list[StoredUtterance] = field(default_factory=list)


_store = Store()


def _purge_expired() -> None:
    days = get_settings().data_retention_days
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    _store.items = [it for it in _store.items if it.at >= cutoff]


@dataclass
class SaveResult:
    saved: bool
    reason: str
    masked_categories: list[str] = field(default_factory=list)


def save_utterance(text: str, *, guardian_consent: bool) -> SaveResult:
    """마스킹 후 저장. 동의 없으면 저장하지 않고 그 사실을 돌려준다."""
    _purge_expired()

    if not guardian_consent:
        return SaveResult(saved=False, reason="보호자 동의 없음 — 저장하지 않음")

    result = pii.mask(text)
    _store.items.append(
        StoredUtterance(
            text=result.text,
            masked_categories=result.categories,
            at=datetime.now(timezone.utc),
        )
    )
    return SaveResult(saved=True, reason="마스킹 후 저장", masked_categories=result.categories)


def count() -> int:
    _purge_expired()
    return len(_store.items)
