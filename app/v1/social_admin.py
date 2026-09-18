"""안전·운영(명세 23절) — 운영자 확인, 신고 검토, 안전 이벤트 조회.

- 운영자는 `ADMIN_KAKAO_IDS` 허용 목록(카카오 회원번호)으로만 정한다. 목록에 없으면 403 FORBIDDEN.
- 아이 화면에는 내부 분류 점수나 차단 규칙을 노출하지 않는다. 보호자에게도 원문이 아니라 종류와 안내만 준다.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from ..db import get_session
from ..models import SafetyEvent
from .cursor import iso
from .deps import CurrentUser, require_user
from .errors import ApiError
from .models_auth import AuthIdentity
from .models_conversation import ChildProfile
from .models_social import CommunityReport, PublicStory, ShareRequest
from .social_schemas import AdminReportItem, AdminSafetyEventOut, SafetyEventOut

# 보호자에게 보여 줄 사건 종류와 안내 문구. 목록에 없는 종류는 보호자에게 올리지 않는다.
GUARDIAN_GUIDANCE = {
    "self_harm": "아이가 힘든 마음을 내비쳤어요. 오늘 밤 아이와 조용히 이야기 나눠 주세요.",
    "violence": "무서운 표현이 대화에 나왔어요. 어떤 이야기였는지 아이와 함께 살펴봐 주세요.",
    "sexual": "나이에 맞지 않는 표현이 있었어요. 아이와 편하게 이야기해 주세요.",
    "personal_info": "아이가 개인 정보를 말하려고 했어요. 인터넷에서 지킬 것을 함께 정해 주세요.",
    "moderation": "대화에서 조심할 표현이 감지돼 티키가 주제를 돌렸어요.",
}
GUARDIAN_NOTICE = "감지된 종류와 안내만 보여 드립니다. 아이가 실제로 쓴 문장은 담지 않습니다."
NEEDS_ATTENTION = ("self_harm",)


def is_admin(db: Session, cu: CurrentUser) -> bool:
    allowlist = get_settings().admin_kakao_ids
    if not allowlist:
        return False
    kakao_id = db.scalar(
        select(AuthIdentity.provider_user_id).where(AuthIdentity.user_id == cu.id, AuthIdentity.provider == "KAKAO")
    )
    return bool(kakao_id) and kakao_id in allowlist


def require_admin(cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)) -> CurrentUser:
    if not is_admin(db, cu):
        raise ApiError(403, "FORBIDDEN", "운영자만 볼 수 있어요.")
    return cu


# --- 신고 검토 --------------------------------------------------------------------


def resolve(db: Session, cu: CurrentUser, row: CommunityReport, resolution: str, note: str | None) -> CommunityReport:
    public = db.get(PublicStory, row.public_story_id)
    row.status = "RESOLVED"
    row.resolution = resolution
    row.resolution_note = note
    row.resolved_by = cu.id
    row.resolved_at = clock.now()
    if public is None:
        return row
    request = db.get(ShareRequest, public.share_request_id)
    if resolution == "KEEP":
        public.status = "PUBLISHED"
        public.hidden_at = None
        if request is not None and request.status == "HIDDEN":
            request.status = "PUBLISHED"
    else:
        public.status = "HIDDEN" if resolution == "HIDE" else "DELETED"
        public.hidden_at = clock.now()
        if resolution == "DELETE":
            # 공개본만 지운다. 아이의 원본 이야기(story_records)는 그대로 둔다.
            public.body = ""
            public.excerpt = ""
            public.thought_journey = {}
        if request is not None:
            request.status = "HIDDEN" if resolution == "HIDE" else "REVOKED"
    return row


def admin_report_out(db: Session, row: CommunityReport) -> AdminReportItem:
    public = db.get(PublicStory, row.public_story_id)
    return AdminReportItem(
        id=row.id,
        public_story_id=row.public_story_id,
        story_title=public.title if public else "",
        reason=row.reason,
        detail=row.detail,
        status=row.status,
        resolution=row.resolution,
        story_status=public.status if public else "DELETED",
        story_report_count=public.report_count if public else 0,
        created_at=iso(row.created_at) or "",
        resolved_at=iso(row.resolved_at),
    )


# --- 안전 이벤트 ------------------------------------------------------------------


def guardian_event_out(event: SafetyEvent) -> SafetyEventOut:
    return SafetyEventOut(
        id=event.id,
        category=event.category,
        needs_attention=bool(event.escalate) or event.category in NEEDS_ATTENTION,
        guidance=GUARDIAN_GUIDANCE.get(event.category, "티키가 대화를 안전한 쪽으로 돌렸어요."),
        occurred_at=iso(event.created_at) or "",
    )


def admin_event_out(db: Session, event: SafetyEvent) -> AdminSafetyEventOut:
    profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == event.child_id))
    return AdminSafetyEventOut(
        **guardian_event_out(event).model_dump(),
        profile_id=profile.id if profile else None,
        user_id=profile.user_id if profile else None,
    )
