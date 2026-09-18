"""v1 안전과 운영(명세 23절) — 보호자 안전 이벤트, 운영자 신고 검토·고위험 이벤트.

운영자는 `ADMIN_KAKAO_IDS` 허용 목록으로만 정한다. 목록에 없으면 403 FORBIDDEN.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ...db import get_session
from ...models import SafetyEvent
from .. import cursor, social_admin
from ..deps import CurrentUser, ProfileScope, require_profile
from ..errors import ApiError
from ..models_social import CommunityReport
from ..social_schemas import (
    AdminReportList,
    AdminSafetyEventList,
    ResolveRequest,
    ResolveResponse,
    SafetyEventList,
)

router = APIRouter(tags=["v1-admin"])


@router.get("/guardian/safety-events", response_model=SafetyEventList)
def guardian_safety_events(
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    """보호자에게는 안내가 필요한 종류와 문구만 준다. 아이가 쓴 문장은 담지 않는다."""
    if "VIEW_REPORTS" not in scope.permissions:
        raise ApiError(403, "FORBIDDEN", "안전 기록을 볼 권한이 없어요.")
    size = cursor.clamp_limit(limit)
    stmt = select(SafetyEvent).where(
        SafetyEvent.child_id == scope.child.id, SafetyEvent.category.in_(tuple(social_admin.GUARDIAN_GUIDANCE))
    )
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(or_(SafetyEvent.created_at < at, and_(SafetyEvent.created_at == at, SafetyEvent.id < row_id)))
    rows = list(db.scalars(stmt.order_by(SafetyEvent.created_at.desc(), SafetyEvent.id.desc()).limit(size + 1)))
    page = rows[:size]
    return SafetyEventList(
        items=[social_admin.guardian_event_out(e) for e in page],
        next_cursor=cursor.encode(page[-1].created_at, page[-1].id) if len(rows) > size else None,
        notice=social_admin.GUARDIAN_NOTICE,
    )


@router.get("/admin/community/reports", response_model=AdminReportList)
def admin_community_reports(
    status: str = Query(default="OPEN", pattern="^(OPEN|RESOLVED|ALL)$"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    admin: CurrentUser = Depends(social_admin.require_admin),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    stmt = select(CommunityReport)
    if status != "ALL":
        stmt = stmt.where(CommunityReport.status == status)
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(
            or_(CommunityReport.created_at < at, and_(CommunityReport.created_at == at, CommunityReport.id < row_id))
        )
    rows = list(db.scalars(stmt.order_by(CommunityReport.created_at.desc(), CommunityReport.id.desc()).limit(size + 1)))
    page = rows[:size]
    return AdminReportList(
        items=[social_admin.admin_report_out(db, r) for r in page],
        next_cursor=cursor.encode(page[-1].created_at, page[-1].id) if len(rows) > size else None,
    )


@router.post("/admin/community/reports/{report_id}/resolve", response_model=ResolveResponse)
def resolve_community_report(
    report_id: str,
    req: ResolveRequest,
    admin: CurrentUser = Depends(social_admin.require_admin),
    db: Session = Depends(get_session),
):
    row = db.get(CommunityReport, report_id)
    if row is None:
        raise ApiError(404, "REPORT_NOT_FOUND", "신고를 찾을 수 없어요.")
    social_admin.resolve(db, admin, row, req.resolution, req.note)
    body = ResolveResponse(report=social_admin.admin_report_out(db, row))
    db.commit()
    return body


@router.get("/admin/safety-events", response_model=AdminSafetyEventList)
def admin_safety_events(
    escalated_only: bool = Query(default=True, alias="escalatedOnly", description="고위험 건만"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    admin: CurrentUser = Depends(social_admin.require_admin),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    stmt = select(SafetyEvent)
    if escalated_only:
        stmt = stmt.where(SafetyEvent.escalate.is_(True))
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(or_(SafetyEvent.created_at < at, and_(SafetyEvent.created_at == at, SafetyEvent.id < row_id)))
    rows = list(db.scalars(stmt.order_by(SafetyEvent.created_at.desc(), SafetyEvent.id.desc()).limit(size + 1)))
    page = rows[:size]
    return AdminSafetyEventList(
        items=[social_admin.admin_event_out(db, e) for e in page],
        next_cursor=cursor.encode(page[-1].created_at, page[-1].id) if len(rows) > size else None,
    )
