"""재시도 안전 — 같은 `Idempotency-Key`(또는 메시지의 `clientMessageId`)는 처음 응답을 그대로 돌려준다.

성공 응답만 기록한다. 오류(409·422 등)는 기록하지 않으므로 같은 요청을 다시 보내면 다시 판정한다.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import ApiError
from .models_conversation import IdempotencyRecord

MAX_KEY = 128


def _check(key: str | None) -> str | None:
    if key is None:
        return None
    key = key.strip()
    if not key or len(key) > MAX_KEY:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["Idempotency-Key"]})
    return key


def replay(db: Session, user_id: str, key: str | None, route: str) -> JSONResponse | None:
    key = _check(key)
    if key is None:
        return None
    record = db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.user_id == user_id, IdempotencyRecord.key == key, IdempotencyRecord.route == route
        )
    )
    if record is None:
        return None
    return JSONResponse(content=record.body, status_code=record.status_code, headers={"Idempotent-Replayed": "true"})


def remember(db: Session, user_id: str, key: str | None, route: str, body: BaseModel, status_code: int = 200) -> None:
    """commit 은 호출자가 한다(응답을 만든 트랜잭션과 같이 저장)."""
    key = _check(key)
    if key is None:
        return
    db.add(
        IdempotencyRecord(
            user_id=user_id,
            key=key,
            route=route,
            status_code=status_code,
            body=body.model_dump(mode="json", by_alias=True),
        )
    )
