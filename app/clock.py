"""시간은 여기서만 읽는다. 테스트는 now 를 바꿔 끼운다.

DB 에는 시간대 없는 UTC 로 저장하고, 요일·날짜 판단만 한국 시간(KST)으로 한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def kst(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc).astimezone(KST)
