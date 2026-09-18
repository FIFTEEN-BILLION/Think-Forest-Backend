"""v1 내 데이터 열람·내보내기·삭제(명세 22절).

- `GET  /data/overview` — 저장된 개수와 보관기간
- `POST /data-exports` → `GET /data-exports/{id}` → `GET /data-exports/{id}/download?token=…`(1회용)
- `POST /data-deletion-requests` · `GET` · `POST …/cancel` — 아이 데이터 삭제(유예기간 안에는 취소 가능)
- `POST /account-deletion-requests` · `GET` · `POST …/cancel` — 로그인 계정 탈퇴

아이 프로필 데이터 삭제와 계정 탈퇴는 다른 일이다. 전체 삭제는 보호자 권한(`MANAGE_DATA`)과 재인증을 요구하고,
요청 즉시 화면에서 숨긴 뒤 유예기간이 지나면 실제로 지운다. 감사 기록에는 요청자·대상 ID·시각·결과만 남는다.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...auth import hash_token
from ...config import get_settings
from ...db import get_session
from ...schemas.common import CamelModel
from .. import cursor, idempotency, jobs, ops_data, ops_notify
from ..deps import CurrentUser, require_user
from ..errors import ApiError
from ..models import AccessToken, prefixed_id
from ..models_conversation import ChildProfile
from ..models_ops import DataJob, DeletionRequest
from ..permissions import ALL_PERMISSIONS

router = APIRouter(tags=["v1-data-rights"])

Scope = Literal["ALL_CHILD_DATA", "CONVERSATIONS", "STORIES", "WORDBOOK", "EXPORTS"]
Section = Literal["PROFILE", "CONVERSATIONS", "STORIES", "WORDBOOK", "REPORTS"]
Reason = Literal["USER_REQUEST", "NO_LONGER_USED", "PRIVACY_CONCERN", "OTHER"]


# ---------------------------------------------------------------- 요청·응답 모델


class DataOverview(CamelModel):
    profile_id: str | None = None
    counts: dict[str, int]
    retention: dict[str, int]
    hidden_scopes: list[str] = Field(default_factory=list, description="삭제를 요청해 지금 숨긴 범위")
    pending_deletion_request_id: str | None = None
    generated_at: str


class ExportRequest(CamelModel):
    profile_id: str | None = Field(default=None, max_length=48)
    format: Literal["JSON"] = "JSON"
    include: list[Section] = Field(default_factory=lambda: list(ops_data.SECTIONS))


class Download(CamelModel):
    url: str
    token: str
    expires_at: str


class ExportDetail(CamelModel):
    job: jobs.Job
    include: list[str]
    byte_size: int
    download: Download | None = None


class DeletionRequestBody(CamelModel):
    profile_id: str | None = Field(default=None, max_length=48)
    scope: Scope = "ALL_CHILD_DATA"
    confirmation: str = Field(description="정확히 `DELETE` 여야 한다")
    reason: Reason = "USER_REQUEST"


class AccountDeletionRequestBody(CamelModel):
    confirmation: str = Field(description="정확히 `DELETE` 여야 한다")
    reason: Reason = "USER_REQUEST"


class DeletionRequestOut(CamelModel):
    id: str
    kind: Literal["DATA", "ACCOUNT"]
    scope: str
    status: jobs.JobStatus
    reason: str
    profile_id: str | None = None
    target_id: str | None = Field(default=None, description="삭제 대상 아이 ID(감사 기록)")
    hidden_at: str | None = None
    effective_at: str
    completed_at: str | None = None
    cancelled_at: str | None = None
    cancellable: bool
    result: dict[str, int] = Field(default_factory=dict, description="지운 개수만. 지운 본문은 남기지 않는다")
    created_at: str


class DeletionRequestResponse(CamelModel):
    request: DeletionRequestOut


# ---------------------------------------------------------------- 공통 확인


def require_reauth(
    authorization: str | None = Header(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> CurrentUser:
    """삭제·탈퇴는 재인증을 요구한다(명세 22절). 발급한 지 오래된 토큰이면 다시 로그인하게 한다."""
    raw = (authorization or "").removeprefix("Bearer ").strip()
    token = db.scalar(select(AccessToken).where(AccessToken.token_hash == hash_token(raw)))
    max_age = get_settings().ops_reauth_max_age_seconds
    if token is None or (clock.now() - token.created_at).total_seconds() > max_age:
        raise ApiError(
            401, "REAUTH_REQUIRED", "안전을 위해 다시 로그인한 뒤 진행해 주세요.", {"maxAgeSeconds": max_age}
        )
    return cu


def _require_manage_data(cu: CurrentUser) -> None:
    """보호자의 `MANAGE_DATA` 권한(명세 25절). B1 트랙이 연결 관계를 넓히면 여기만 바뀐다."""
    if "MANAGE_DATA" not in ALL_PERMISSIONS:  # pragma: no cover - 권한표가 줄어들면 잡힌다
        raise ApiError(403, "FORBIDDEN", "데이터를 관리할 권한이 없어요.", {"permission": "MANAGE_DATA"})


def _own_profile_id(db: Session, cu: CurrentUser, requested: str | None) -> str | None:
    """URL·본문의 profileId 를 그대로 믿지 않고 연결 관계로 확인한다(명세 25절)."""
    profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == cu.child.id))
    if requested and (profile is None or profile.id != requested):
        raise ApiError(404, "PROFILE_NOT_FOUND", "아이 프로필을 찾을 수 없어요.", {"profileId": requested})
    return profile.id if profile else None


def _confirm(value: str) -> None:
    if value != "DELETE":
        raise ApiError(400, "INVALID_INPUT", "`DELETE` 를 정확히 입력해 주세요.", {"fields": ["confirmation"]})


# ---------------------------------------------------------------- 22.1 열람


@router.get("/data/overview", response_model=DataOverview)
def data_overview(cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)) -> DataOverview:
    ops_data.run_due(db)
    pending = db.scalar(
        select(DeletionRequest)
        .where(
            DeletionRequest.user_id == cu.id,
            DeletionRequest.status == "QUEUED",
            DeletionRequest.kind == "DATA",
            ((DeletionRequest.kind == "ACCOUNT") | (DeletionRequest.child_id == cu.child.id)),
        )
        .order_by(DeletionRequest.created_at.desc())
    )
    return DataOverview(
        profile_id=_own_profile_id(db, cu, None),
        counts=ops_data.counts(db, cu.user, cu.child),
        retention=ops_data.retention(),
        hidden_scopes=sorted(ops_data.hidden_scopes(db, cu.id, cu.child.id)),
        pending_deletion_request_id=pending.id if pending else None,
        generated_at=cursor.iso(clock.now()) or "",
    )


# ---------------------------------------------------------------- 22.2 내보내기


@router.post("/data-exports", response_model=jobs.JobResponse, status_code=202)
def create_export(
    body: ExportRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    """작업을 만들고 `202` 로 돌려준다. 내보낼 JSON 은 요청 안에서 만들어 DB 에 둔다(파일 없음)."""
    _require_manage_data(cu)
    route = "POST /data-exports"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    ops_data.run_due(db)
    profile_id = _own_profile_id(db, cu, body.profile_id)
    job = DataJob(
        user_id=cu.id,
        profile_id=profile_id,
        type="DATA_EXPORT",
        status="RUNNING",
        format=body.format,
        include=list(body.include),
    )
    db.add(job)
    db.flush()
    try:
        payload = ops_data.build_export(db, cu.user, cu.child, list(body.include))
    except Exception:  # noqa: BLE001 — 실패도 작업 상태로 알린다(원문은 남기지 않는다)
        job.status = "FAILED"
        job.error_code = "EXPORT_FAILED"
        job.completed_at = clock.now()
        db.commit()
        return jobs.JobResponse(job=jobs.job_out(job))
    job.payload = payload
    job.byte_size = len(json.dumps(payload, ensure_ascii=False).encode())
    job.status = "SUCCEEDED"
    job.completed_at = clock.now()
    ops_notify.notify(db, cu.id, "DATA_EXPORT_READY", {"exportId": job.id})
    out = jobs.JobResponse(job=jobs.job_out(job))
    idempotency.remember(db, cu.id, idempotency_key, route, out, status_code=202)
    db.commit()
    return out


def _own_job(db: Session, cu: CurrentUser, export_id: str) -> DataJob:
    job = db.get(DataJob, export_id)
    if job is None or job.user_id != cu.id:
        raise ApiError(404, "EXPORT_NOT_FOUND", "내보내기 작업을 찾을 수 없어요.")
    return job


@router.get("/data-exports/{export_id}", response_model=ExportDetail)
def get_export(
    export_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> ExportDetail:
    """끝난 작업이면 짧게 살고 한 번만 쓰는 내려받기 토큰을 함께 준다."""
    job = _own_job(db, cu, export_id)
    download = None
    if job.status == "SUCCEEDED":
        raw, expires_at = ops_data.issue_download(db, job)
        download = Download(url=f"/api/v1/data-exports/{job.id}/download?token={raw}", token=raw, expires_at=expires_at)
        db.commit()
    return ExportDetail(
        job=jobs.job_out(job), include=list(job.include or []), byte_size=job.byte_size, download=download
    )


@router.get(
    "/data-exports/{export_id}/download",
    responses={200: {"content": {"application/json": {}}, "description": "내보내기 JSON 파일"}},
    response_class=Response,
)
def download_export(export_id: str, token: str = Query(...), db: Session = Depends(get_session)) -> Response:
    """토큰만으로 받는다(브라우저가 바로 열 수 있게). 토큰은 한 번 쓰면 사라진다."""
    try:
        job = ops_data.take_download(db, export_id, token)
    except ops_data.DownloadInvalid as exc:
        raise ApiError(404, "DOWNLOAD_INVALID", "내려받기 주소가 만료됐어요. 다시 받아 주세요.") from exc
    body = json.dumps(job.payload or {}, ensure_ascii=False, indent=2).encode()
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{job.id}.json"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------- 22.3 삭제


def _request_out(row: DeletionRequest) -> DeletionRequestOut:
    return DeletionRequestOut(
        id=row.id,
        kind=row.kind,
        scope=row.scope,
        status=row.status,
        reason=row.reason,
        profile_id=row.profile_id,
        target_id=row.child_id,
        hidden_at=cursor.iso(row.hidden_at),
        effective_at=cursor.iso(row.effective_at) or "",
        completed_at=cursor.iso(row.completed_at),
        cancelled_at=cursor.iso(row.cancelled_at),
        cancellable=row.status == "QUEUED",
        result=row.result or {},
        created_at=cursor.iso(row.created_at) or "",
    )


def _create_request(
    db: Session, cu: CurrentUser, *, kind: str, scope: str, reason: str, profile_id: str | None, grace_days: int
) -> DeletionRequest:
    now = clock.now()
    row = DeletionRequest(
        id=prefixed_id("del" if kind == "DATA" else "acc"),
        user_id=cu.id,
        kind=kind,
        profile_id=profile_id,
        child_id=cu.child.id,
        scope=scope,
        reason=reason,
        requested_by=cu.id,
        status="QUEUED",
        hidden_at=now,  # 요청 즉시 화면에서 숨긴다
        effective_at=now + timedelta(days=grace_days),
    )
    db.add(row)
    db.flush()
    ops_notify.notify(db, cu.id, "DELETION_SCHEDULED", {"requestId": row.id, "kind": kind})
    db.commit()
    return row


def _own_request(db: Session, cu: CurrentUser, request_id: str, kind: str) -> DeletionRequest:
    row = db.get(DeletionRequest, request_id)
    if row is None or row.user_id != cu.id or row.kind != kind:
        raise ApiError(404, "DELETION_REQUEST_NOT_FOUND", "삭제 요청을 찾을 수 없어요.")
    return row


def _cancel(db: Session, cu: CurrentUser, row: DeletionRequest) -> DeletionRequest:
    if row.status != "QUEUED" or row.effective_at <= clock.now():
        raise ApiError(409, "DELETION_NOT_CANCELLABLE", "유예기간이 지나 취소할 수 없어요.", {"status": row.status})
    row.status = "CANCELLED"
    row.cancelled_at = clock.now()
    row.hidden_at = None  # 다시 보이게 한다
    db.commit()
    return row


@router.post("/data-deletion-requests", response_model=DeletionRequestResponse, status_code=202)
def create_data_deletion(
    body: DeletionRequestBody,
    cu: CurrentUser = Depends(require_reauth),
    db: Session = Depends(get_session),
) -> DeletionRequestResponse:
    _require_manage_data(cu)
    _confirm(body.confirmation)
    ops_data.run_due(db)
    profile_id = _own_profile_id(db, cu, body.profile_id)
    row = _create_request(
        db,
        cu,
        kind="DATA",
        scope=body.scope,
        reason=body.reason,
        profile_id=profile_id,
        grace_days=get_settings().data_deletion_grace_days,
    )
    return DeletionRequestResponse(request=_request_out(row))


@router.get("/data-deletion-requests/{request_id}", response_model=DeletionRequestResponse)
def get_data_deletion(
    request_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> DeletionRequestResponse:
    ops_data.run_due(db)
    return DeletionRequestResponse(request=_request_out(_own_request(db, cu, request_id, "DATA")))


@router.post("/data-deletion-requests/{request_id}/cancel", response_model=DeletionRequestResponse)
def cancel_data_deletion(
    request_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> DeletionRequestResponse:
    return DeletionRequestResponse(request=_request_out(_cancel(db, cu, _own_request(db, cu, request_id, "DATA"))))


@router.post("/account-deletion-requests", response_model=DeletionRequestResponse, status_code=202)
def create_account_deletion(
    body: AccountDeletionRequestBody,
    cu: CurrentUser = Depends(require_reauth),
    db: Session = Depends(get_session),
) -> DeletionRequestResponse:
    """계정 탈퇴. 연결된 아이 프로필 데이터도 함께 지우되, 다른 보호자 연결이 있으면 프로필은 남긴다."""
    _confirm(body.confirmation)
    ops_data.run_due(db)
    row = _create_request(
        db,
        cu,
        kind="ACCOUNT",
        scope="ACCOUNT",
        reason=body.reason,
        profile_id=_own_profile_id(db, cu, None),
        grace_days=get_settings().account_deletion_grace_days,
    )
    return DeletionRequestResponse(request=_request_out(row))


@router.get("/account-deletion-requests/{request_id}", response_model=DeletionRequestResponse)
def get_account_deletion(
    request_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> DeletionRequestResponse:
    ops_data.run_due(db)
    return DeletionRequestResponse(request=_request_out(_own_request(db, cu, request_id, "ACCOUNT")))


@router.post("/account-deletion-requests/{request_id}/cancel", response_model=DeletionRequestResponse)
def cancel_account_deletion(
    request_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> DeletionRequestResponse:
    return DeletionRequestResponse(request=_request_out(_cancel(db, cu, _own_request(db, cu, request_id, "ACCOUNT"))))
