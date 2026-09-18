"""오래 걸리는 작업(내보내기·삭제)의 공통 상태값. B5 트랙이 jobs 테이블과 함께 채운다."""

from __future__ import annotations

from typing import Literal

JobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]
JobType = Literal["DATA_EXPORT", "DATA_DELETION", "ACCOUNT_DELETION"]
