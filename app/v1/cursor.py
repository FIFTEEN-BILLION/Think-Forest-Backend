"""목록 커서 — 불투명 base64(`updatedAt|id`). 잘못된 커서는 400 INVALID_INPUT."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime

from .errors import ApiError

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def iso(value: datetime | None) -> str | None:
    """UTC naive → ISO 8601 `Z` 문자열(초 단위)."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def clamp_limit(limit: int | None, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return default
    if limit < 1 or limit > maximum:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["limit"]})
    return limit


def encode(updated_at: datetime, row_id: str) -> str:
    return base64.urlsafe_b64encode(f"{updated_at.isoformat()}|{row_id}".encode()).decode().rstrip("=")


def decode(raw: str) -> tuple[datetime, str]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        stamp, row_id = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        return datetime.fromisoformat(stamp), row_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["cursor"]}) from exc


def encode_offset(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset|{offset}".encode()).decode().rstrip("=")


def decode_offset(raw: str | None) -> int:
    if not raw:
        return 0
    try:
        padded = raw + "=" * (-len(raw) % 4)
        label, value = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        offset = int(value)
        if label != "offset" or offset < 0:
            raise ValueError(label)
        return offset
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["cursor"]}) from exc
