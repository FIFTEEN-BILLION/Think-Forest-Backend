"""v1 계정·프로필·보호자 연결·동의 요청·응답 스키마. JSON 은 camelCase.

`schemas_conversation.py` 와 겹치지 않게 계정 쪽만 여기 둔다(트랙 사이 충돌 방지).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..schemas.common import CamelModel
from .schemas_conversation import MeProfile, MeUser

Theme = Literal["AUTO", "LIGHT", "DARK"]  # 명세 17절
RetentionDays = Literal[30, 90, 180, 365]
PermissionName = Literal["VIEW_PROFILE", "VIEW_STORIES", "VIEW_REPORTS", "REVIEW_SHARING", "MANAGE_DATA"]


# --- 프로필 --------------------------------------------------------------------


class ProfileOut(CamelModel):
    id: str
    nickname: str
    school_or_group: str | None = None
    grade_or_age_band: str | None = None
    interests: list[str] = Field(default_factory=list)
    interest_details: list[str] = Field(default_factory=list)
    growth_goal: str | None = None
    summary: str = ""
    version: int
    role: Literal["OWNER", "GUARDIAN"]
    permissions: list[str]
    is_default: bool
    needs_first_greeting: bool
    created_at: str
    updated_at: str


class ProfileResponse(CamelModel):
    profile: ProfileOut


class ProfileListResponse(CamelModel):
    items: list[ProfileOut]
    next_cursor: str | None = None


class ProfileCreateRequest(CamelModel):
    nickname: str = Field(min_length=1, max_length=20)
    school_or_group: str | None = Field(default=None, max_length=20)
    grade_or_age_band: str | None = Field(default=None, max_length=30)
    interests: list[str] = Field(default_factory=list, max_length=5)
    growth_goal: str | None = Field(default=None, max_length=60)
    make_default: bool = False


class ProfileUpdateRequest(CamelModel):
    """보낸 키만 바꾼다. `version` 은 If-Match 헤더 대신 쓸 수 있다."""

    nickname: str | None = Field(default=None, min_length=1, max_length=20)
    school_or_group: str | None = Field(default=None, max_length=20)
    grade_or_age_band: str | None = Field(default=None, max_length=30)
    interests: list[str] | None = Field(default=None, max_length=5)
    growth_goal: str | None = Field(default=None, max_length=60)
    make_default: bool | None = Field(default=None, description="이 프로필을 이 계정의 기본 프로필로")
    version: int | None = Field(default=None, ge=1)


# --- 프로필 설정 ----------------------------------------------------------------


class SettingsOut(CamelModel):
    profile_id: str
    voice_enabled: bool = Field(description="마이크·읽어주기 사용 허용. 미설정 시 true")
    tts_enabled: bool
    guardian_preview_enabled: bool
    theme: Theme
    retention_days: int
    version: int
    updated_at: str


class RetentionNotice(CamelModel):
    """보관 기간을 바꾸면 무엇이 언제 지워지는지 그대로 알려 준다(명세 17절)."""

    previous_days: int
    retention_days: int
    effective_at: str
    deletes_before: str
    deletes_now: bool
    targets: list[str]
    message: str


class SettingsResponse(CamelModel):
    settings: SettingsOut
    retention_notice: RetentionNotice | None = None


class SettingsUpdateRequest(CamelModel):
    voice_enabled: bool | None = Field(default=None, description="보호자 음성 사용 설정. 생략하면 기존 값 유지")
    tts_enabled: bool | None = None
    guardian_preview_enabled: bool | None = None
    theme: Theme | None = None
    retention_days: RetentionDays | None = None
    version: int | None = Field(default=None, ge=1)


# --- 보호자 연결 ----------------------------------------------------------------


class LinkOut(CamelModel):
    id: str
    profile_id: str
    user_id: str
    role: Literal["OWNER", "GUARDIAN"]
    permissions: list[str]
    status: Literal["ACTIVE", "REVOKED"]
    created_at: str
    revoked_at: str | None = None


class LinkResponse(CamelModel):
    link: LinkOut


class LinkListResponse(CamelModel):
    items: list[LinkOut]
    next_cursor: str | None = None


class LinkUpdateRequest(CamelModel):
    permissions: list[PermissionName] = Field(min_length=1)


class UnlinkResponse(CamelModel):
    ok: bool = True
    link_id: str
    data_deleted: bool = False
    message: str = "연결만 해제했어요. 아이 기록은 그대로 남아 있어요."


class InvitationCreateRequest(CamelModel):
    profile_id: str = Field(min_length=1, max_length=48)
    permissions: list[PermissionName] | None = None
    expires_in_minutes: int | None = Field(default=None, ge=5, le=1440, description="비우면 서버 기본값(30분)")


class InvitationOut(CamelModel):
    id: str
    profile_id: str
    token: str = Field(description="1회용 초대 토큰. 이 응답에서만 볼 수 있다")
    permissions: list[str]
    expires_at: str
    created_at: str


class InvitationResponse(CamelModel):
    invitation: InvitationOut


class InvitationAcceptResponse(CamelModel):
    link: LinkOut
    profile: ProfileOut


class GuardianChildOut(CamelModel):
    profile_id: str
    link_id: str
    nickname: str
    grade_or_age_band: str | None = None
    role: Literal["OWNER", "GUARDIAN"]
    permissions: list[str]
    is_default: bool
    needs_first_greeting: bool
    updated_at: str


class GuardianChildListResponse(CamelModel):
    items: list[GuardianChildOut]
    next_cursor: str | None = None


# --- 법률 문서·동의 --------------------------------------------------------------


class LegalDocumentOut(CamelModel):
    id: str
    title: str
    version: str
    locale: str
    required: bool
    summary: str
    body: str
    draft: bool = True
    draft_notice: str


class LegalDocumentListResponse(CamelModel):
    items: list[LegalDocumentOut]
    next_cursor: str | None = None


class ConsentActor(CamelModel):
    user_id: str
    role: Literal["GUARDIAN"]


class ConsentOut(CamelModel):
    id: str
    profile_id: str
    document_id: str
    document_version: str
    status: Literal["GRANTED", "REVOKED"]
    current: bool = Field(description="철회되지 않았고 지금 쓰는 문서 버전과 같은지")
    actor: ConsentActor
    granted_at: str
    revoked_at: str | None = None


class ConsentResponse(CamelModel):
    consent: ConsentOut


class ConsentWriteResponse(CamelModel):
    """`items` 순서대로 기록 결과. `agreed: false` 는 이미 한 동의를 철회한다."""

    items: list[ConsentOut]


class ConsentListResponse(CamelModel):
    items: list[ConsentOut]
    next_cursor: str | None = None


class ConsentItemRequest(CamelModel):
    document_id: str = Field(min_length=1, max_length=40)
    version: str | None = Field(
        default=None, max_length=20, description="일반 계정은 생략 시 현재 버전. 게스트 동의 등록 시 필수"
    )
    agreed: bool = True


class ConsentCreateRequest(CamelModel):
    """명세 26절. `actor` 는 받기만 하고 믿지 않는다 — 서버가 로그인 계정과 연결 권한에서 뽑는다."""

    profile_id: str = Field(min_length=1, max_length=48)
    items: list[ConsentItemRequest] = Field(min_length=1, max_length=10)
    actor: str | None = Field(default=None, description="서버는 이 값을 쓰지 않는다")
    guardian_confirmed: bool = Field(
        default=False,
        description="보호자가 직접 확인했는지. 게스트의 agreed=true 항목이 있으면 true 필수(403). 본인인증 여부가 아님",
    )


# --- /me ------------------------------------------------------------------------


class MeProfileItem(CamelModel):
    id: str
    nickname: str
    grade_or_age_band: str | None = None
    interests: list[str] = Field(default_factory=list)
    growth_goal: str | None = None
    role: Literal["OWNER", "GUARDIAN"]
    permissions: list[str]
    is_default: bool
    needs_first_greeting: bool


class AccountMeResponse(CamelModel):
    """신규 계정의 빈 기본 프로필은 profiles에 포함된다. profile은 첫인사 완료 전 null이다."""

    user: MeUser
    profile: MeProfile | None
    profiles: list[MeProfileItem]
