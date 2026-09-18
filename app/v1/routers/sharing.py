"""v1 이야기 공유와 보호자 승인(명세 15절).

아이가 요청 → 보호자가 내용을 읽고 버전을 확인해 승인 → 공개본 생성. 승인 전에는 아이가 취소할 수 있다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ...db import get_session
from .. import cursor, idempotency, social_sharing
from ..deps import CurrentUser, ProfileScope, require_profile, require_user
from ..errors import ApiError
from ..models_social import ShareRequest
from ..social_schemas import (
    ApproveRequest,
    RejectRequest,
    RevokeRequest,
    ShareRequestCreate,
    ShareRequestList,
    ShareRequestResponse,
)

router = APIRouter(tags=["v1-sharing"])


def _guardian(scope: ProfileScope) -> CurrentUser:
    """보호자 권한 확인. 연결 관계에 `REVIEW_SHARING` 이 없으면 403."""
    if "REVIEW_SHARING" not in scope.permissions:
        raise ApiError(403, "FORBIDDEN", "공유를 검토할 권한이 없어요.")
    return CurrentUser(user=scope.user, child=scope.child)


def _response(db: Session, request: ShareRequest) -> ShareRequestResponse:
    public = social_sharing.public_of(db, request)
    social_sharing.sync_edits(db, request, public)
    return ShareRequestResponse(
        share_request=social_sharing.request_out(request),
        public_story=social_sharing.public_out(public) if public and public.status == "PUBLISHED" else None,
    )


@router.post("/stories/{story_id}/share-requests", response_model=ShareRequestResponse, status_code=201)
def create_share_request(
    story_id: str,
    req: ShareRequestCreate,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    route = f"POST share-requests {story_id}"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    story = social_sharing.own_story(db, cu, story_id)
    request = social_sharing.create(db, cu, story, req.audience, req.hide_profile)
    body = _response(db, request)
    idempotency.remember(db, cu.id, idempotency_key, route, body, status_code=201)
    db.commit()
    return body


@router.get("/share-requests/{request_id}", response_model=ShareRequestResponse)
def get_share_request(request_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    request = social_sharing.own_request(db, cu, request_id)
    body = _response(db, request)
    db.commit()
    return body


@router.delete("/share-requests/{request_id}", response_model=ShareRequestResponse)
def cancel_share_request(request_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    request = social_sharing.own_request(db, cu, request_id)
    social_sharing.cancel(request)
    body = _response(db, request)
    db.commit()
    return body


@router.get("/guardian/share-requests", response_model=ShareRequestList)
def list_share_requests(
    status: str | None = Query(default=None, description="쉼표로 여러 개. 기본은 승인 대기(PENDING_GUARDIAN)"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    guardian = _guardian(scope)
    size = cursor.clamp_limit(limit)
    wanted = [s.strip().upper() for s in (status or "PENDING_GUARDIAN").split(",") if s.strip()]
    stmt = select(ShareRequest).where(ShareRequest.user_id == guardian.id)
    if "ALL" not in wanted:
        stmt = stmt.where(ShareRequest.status.in_(wanted))
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(
            or_(ShareRequest.updated_at < at, and_(ShareRequest.updated_at == at, ShareRequest.id < row_id))
        )
    rows = list(db.scalars(stmt.order_by(ShareRequest.updated_at.desc(), ShareRequest.id.desc()).limit(size + 1)))
    page = rows[:size]
    for request in page:
        social_sharing.sync_edits(db, request, social_sharing.public_of(db, request))
    body = ShareRequestList(
        items=[social_sharing.request_out(r) for r in page],
        next_cursor=cursor.encode(page[-1].updated_at, page[-1].id) if len(rows) > size else None,
    )
    db.commit()
    return body


def _pending(db: Session, scope: ProfileScope, request_id: str) -> ShareRequest:
    guardian = _guardian(scope)
    request = social_sharing.own_request(db, guardian, request_id)
    social_sharing.sync_edits(db, request, social_sharing.public_of(db, request))
    return request


@router.post("/guardian/share-requests/{request_id}/approve", response_model=ShareRequestResponse)
def approve_share_request(
    request_id: str,
    req: ApproveRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    request = _pending(db, scope, request_id)
    social_sharing.approve(db, scope, request, req.confirmed_body_version, req.confirmed_redactions)
    body = _response(db, request)
    db.commit()
    return body


@router.post("/guardian/share-requests/{request_id}/reject", response_model=ShareRequestResponse)
def reject_share_request(
    request_id: str,
    req: RejectRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    request = _pending(db, scope, request_id)
    social_sharing.reject(db, request, req.reason)
    body = _response(db, request)
    db.commit()
    return body


@router.post("/guardian/share-requests/{request_id}/revoke", response_model=ShareRequestResponse)
def revoke_share_request(
    request_id: str,
    req: RevokeRequest | None = None,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    request = _pending(db, scope, request_id)
    social_sharing.revoke(db, request)
    if req and req.reason:
        request.reject_reason = req.reason
    body = _response(db, request)
    db.commit()
    return body
