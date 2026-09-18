"""v1 공유·친구 이야기·운영 요청/응답 스키마. 키는 camelCase, 시각은 ISO 8601 UTC(`Z`) 문자열."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..schemas.common import CamelModel

Audience = Literal["PEERS", "FAMILY", "INVITED"]
ShareStatus = Literal[
    "DRAFT", "PENDING_GUARDIAN", "APPROVED", "PUBLISHED", "REJECTED", "CANCELLED", "HIDDEN", "REVOKED"
]
PublicStatus = Literal["PUBLISHED", "PAUSED", "HIDDEN", "REVOKED", "DELETED"]
ReportReason = Literal["UNCOMFORTABLE_CONTENT", "SCARY", "PERSONAL_INFO", "COPIED", "MEAN_WORDS", "OTHER"]
RecommendationFilter = Literal["SIMILAR_AGE", "SAME_CATEGORY", "POPULAR", "NEW"]
Resolution = Literal["KEEP", "HIDE", "DELETE"]


# --- 공유 요청 ------------------------------------------------------------------


class ShareRequestCreate(CamelModel):
    audience: Audience = "PEERS"
    hide_profile: bool = True


class ShareRequestOut(CamelModel):
    id: str
    story_id: str
    status: ShareStatus
    audience: Audience
    hide_profile: bool
    requested_body_version: int = Field(description="요청을 올릴 때의 이야기 본문 버전")
    confirmed_body_version: int | None = Field(default=None, description="보호자가 확인하고 승인한 본문 버전")
    public_story_id: str | None = None
    reject_reason: str | None = None
    pending_reason: str | None = Field(
        default=None, description="승인 뒤 본문이 바뀌어 다시 승인을 기다리는 사유(STORY_EDITED)"
    )
    requested_at: str
    decided_at: str | None = None
    updated_at: str


class ShareRequestResponse(CamelModel):
    share_request: ShareRequestOut
    public_story: PublicStoryOut | None = None


class ShareRequestList(CamelModel):
    items: list[ShareRequestOut]
    next_cursor: str | None


class ApproveRequest(CamelModel):
    confirmed_body_version: int = Field(ge=1, description="보호자가 읽고 확인한 본문 버전. 지금 버전과 달라야 409")
    confirmed_redactions: bool = False


class RejectRequest(CamelModel):
    reason: str = Field(min_length=1, max_length=200)


class RevokeRequest(CamelModel):
    reason: str | None = Field(default=None, max_length=200)


# --- 친구들의 이야기 -------------------------------------------------------------


class PublicAuthor(CamelModel):
    display_name: str
    age_band: str = Field(description="넓은 나이대만. 정확한 나이·학년·학교는 주지 않는다")


class PublicStoryOut(CamelModel):
    id: str
    title: str
    excerpt: str
    author: PublicAuthor
    category: str
    recommendation_count: int
    recommended_by_me: bool
    recommendation_reason: str
    guardian_approved: bool = True
    published_at: str


class PublicStoryDetail(PublicStoryOut):
    body: str
    thought_journey: dict
    mine: bool = Field(default=False, description="내가 쓴 이야기인지")


class CommunityList(CamelModel):
    items: list[PublicStoryOut]
    next_cursor: str | None


class CommunityDetailResponse(CamelModel):
    story: PublicStoryDetail


class RecommendationResponse(CamelModel):
    story_id: str
    recommendation_count: int
    recommended_by_me: bool


class CommunityReportCreate(CamelModel):
    reason: ReportReason = "UNCOMFORTABLE_CONTENT"
    detail: str | None = Field(default=None, max_length=300)


class CommunityReportOut(CamelModel):
    id: str
    public_story_id: str
    reason: ReportReason
    detail: str | None
    status: Literal["OPEN", "RESOLVED"]
    resolution: Resolution | None = None
    created_at: str


class CommunityReportResponse(CamelModel):
    report: CommunityReportOut
    story_status: PublicStatus = Field(description="신고 뒤의 공개 상태. 기준을 넘으면 HIDDEN")


# --- 운영 ------------------------------------------------------------------------


class AdminReportItem(CamelModel):
    id: str
    public_story_id: str
    story_title: str
    reason: ReportReason
    detail: str | None
    status: Literal["OPEN", "RESOLVED"]
    resolution: Resolution | None
    story_status: PublicStatus
    story_report_count: int
    created_at: str
    resolved_at: str | None


class AdminReportList(CamelModel):
    items: list[AdminReportItem]
    next_cursor: str | None


class ResolveRequest(CamelModel):
    resolution: Resolution
    note: str | None = Field(default=None, max_length=200)


class ResolveResponse(CamelModel):
    report: AdminReportItem


class SafetyEventOut(CamelModel):
    id: str
    category: str = Field(description="탐지 종류만. 아이 원문은 담지 않는다")
    needs_attention: bool = Field(description="보호자에게 안내가 필요한 사건인지")
    guidance: str
    occurred_at: str


class SafetyEventList(CamelModel):
    items: list[SafetyEventOut]
    next_cursor: str | None
    notice: str


class AdminSafetyEventOut(SafetyEventOut):
    profile_id: str | None
    user_id: str | None


class AdminSafetyEventList(CamelModel):
    items: list[AdminSafetyEventOut]
    next_cursor: str | None


# ShareRequestResponse 가 뒤에 정의된 PublicStoryOut 을 가리킨다.
ShareRequestResponse.model_rebuild()
