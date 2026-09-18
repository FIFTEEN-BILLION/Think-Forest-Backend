"""오래 걸리는 작업(내보내기·삭제)의 공통 상태값과 응답 모양.

명세 27절: 상태는 `QUEUED`·`RUNNING`·`SUCCEEDED`·`FAILED`·`CANCELLED` 로 통일하고,
긴 작업은 `202 Accepted` + 작업 ID 를 돌려준다.
"""

from __future__ import annotations

from typing import Literal

from ..schemas.common import CamelModel
from . import cursor

JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
JobType = Literal["DATA_EXPORT", "DATA_DELETION", "ACCOUNT_DELETION"]

TERMINAL: frozenset[str] = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


class Job(CamelModel):
    """명세 27절 `{"job": {...}}` 안에 들어가는 작업 상태."""

    id: str
    type: JobType
    status: JobStatus
    created_at: str
    updated_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class JobResponse(CamelModel):
    job: Job


def job_out(row) -> Job:
    """DataJob 행 → 응답 모델."""
    return Job(
        id=row.id,
        type=row.type,
        status=row.status,
        created_at=cursor.iso(row.created_at) or "",
        updated_at=cursor.iso(row.updated_at),
        completed_at=cursor.iso(row.completed_at),
        error=row.error_code,
    )
