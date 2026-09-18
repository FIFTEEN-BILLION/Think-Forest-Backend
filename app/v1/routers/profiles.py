"""v1 아이 프로필 — 한 계정(보호자·가족)이 여러 아이 프로필을 갖는다.

- 프로필마다 기존 대화 엔진용 `children` 행이 따로 있다. 기본 프로필은 `users.child_id` 로 이어 둔다.
- 수정은 If-Match(또는 본문 `version`)로 겹쳐 쓰기를 막는다. 버전이 다르면 409 VERSION_CONFLICT.
- 보관 기간을 바꾸면 무엇이 언제 지워지는지 응답으로 알려 준다(명세 17절).
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...auth import permission_enabled
from ...config import get_settings
from ...db import get_session
from ...models import Child
from .. import cursor, idempotency, models_accounts
from ..cursor import iso
from ..deps import CurrentUser, ProfileScope, require_user, resolve_scope
from ..errors import ApiError
from ..models_accounts import ProfileMember, ProfileSettings
from ..models_conversation import ChildProfile
from ..permissions import ALL_PERMISSIONS
from ..schemas_accounts import (
    ProfileCreateRequest,
    ProfileListResponse,
    ProfileOut,
    ProfileResponse,
    ProfileUpdateRequest,
    RetentionNotice,
    SettingsOut,
    SettingsResponse,
    SettingsUpdateRequest,
)

router = APIRouter(prefix="/profiles", tags=["v1-profiles"])

# 보관 기간이 지나면 지워지는 것(명세 17절). 이야기 정리본(책장)은 아이가 지울 때까지 남는다.
RETENTION_TARGETS = ["티키와 나눈 대화 메시지", "대화에서 뽑은 정보", "음성을 글로 바꾼 임시 기록"]


# ---------- 공통 ----------


def profile_out(profile: ChildProfile, member: ProfileMember) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        nickname=profile.nickname,
        school_or_group=profile.school_or_group,
        grade_or_age_band=profile.grade_or_age_band,
        interests=list(profile.interests or []),
        interest_details=list(profile.interest_details or []),
        growth_goal=profile.growth_goal,
        summary=profile.summary or "",
        version=profile.version,
        role=member.role,
        permissions=list(member.permissions or []),
        is_default=member.is_default,
        needs_first_greeting=profile.completed_at is None,
        created_at=iso(profile.created_at) or "",
        updated_at=iso(profile.updated_at) or "",
    )


def scope_out(scope: ProfileScope) -> ProfileOut:
    assert scope.member is not None
    return profile_out(scope.profile, scope.member)


def check_version(if_match: str | None, body_version: int | None, current: int, *, required: bool = True) -> None:
    """If-Match 헤더(또는 본문 version)가 지금 버전과 같아야 바꾼다.

    프로필 수정은 명세 17절 예시대로 If-Match 를 요구한다. 설정은 예시에 헤더가 없어서 있을 때만 확인한다.
    """
    raw = (if_match or "").strip().strip('"')
    if raw == "*":
        return
    if raw:
        if not raw.isdigit():
            raise ApiError(400, "INVALID_INPUT", "If-Match 값을 확인해 주세요.", {"fields": ["If-Match"]})
        expected = int(raw)
    elif body_version is not None:
        expected = body_version
    elif not required:
        return
    else:
        message = "If-Match 헤더나 version 이 필요해요."
        raise ApiError(400, "INVALID_INPUT", message, {"fields": ["If-Match"]})
    if expected != current:
        message = "다른 곳에서 먼저 바뀌었어요. 새로 불러와 주세요."
        raise ApiError(409, "VERSION_CONFLICT", message, {"currentVersion": current})


def settings_of(db: Session, profile: ChildProfile) -> ProfileSettings:
    row = db.scalar(select(ProfileSettings).where(ProfileSettings.profile_id == profile.id))
    if row is None:
        row = ProfileSettings(profile_id=profile.id, retention_days=models_accounts.retention_default())
        db.add(row)
        db.flush()
    return row


def settings_out(row: ProfileSettings, child: Child) -> SettingsOut:
    return SettingsOut(
        profile_id=row.profile_id,
        voice_enabled=permission_enabled(child, "voice"),
        tts_enabled=row.tts_enabled,
        guardian_preview_enabled=row.guardian_preview_enabled,
        theme=row.theme,
        retention_days=row.retention_days,
        version=row.version,
        updated_at=iso(row.updated_at) or "",
    )


def set_default(db: Session, cu: CurrentUser, member: ProfileMember) -> None:
    """기본 프로필을 옮긴다. 기존 엔진이 보는 `users.child_id` 도 같이 옮긴다."""
    if member.role != "OWNER":
        message = "초대로 연결된 프로필은 기본 프로필로 정할 수 없어요."
        raise ApiError(403, "FORBIDDEN", message, {"profileId": member.profile_id})
    for other in models_accounts.active_members(db, cu.user):
        other.is_default = other.id == member.id
    member.is_default = True
    cu.user.child_id = member.child_id
    db.flush()


def sync_child_row(child: Child, profile: ChildProfile) -> None:
    """기존 대화 엔진 개인화용 children 행을 프로필에 맞춘다."""
    child.nickname = (profile.nickname or "")[:20]
    child.likes = list((profile.interests or [])[:5])
    if profile.growth_goal:
        child.want_to_learn = [profile.growth_goal]


# ---------- 목록·생성 ----------


@router.get("", response_model=ProfileListResponse)
def list_profiles(
    limit: int | None = Query(default=None, ge=1, le=50),
    page_cursor: str | None = Query(default=None, alias="cursor"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProfileListResponse:
    members = models_accounts.active_members(db, cu.user)
    size = cursor.clamp_limit(limit)
    offset = cursor.decode_offset(page_cursor)
    page = members[offset : offset + size]
    items = []
    for member in page:
        profile = db.get(ChildProfile, member.profile_id)
        if profile is not None:
            items.append(profile_out(profile, member))
    has_more = offset + size < len(members)
    return ProfileListResponse(items=items, next_cursor=cursor.encode_offset(offset + size) if has_more else None)


@router.post("", response_model=ProfileResponse, status_code=201)
def create_profile(
    req: ProfileCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    replay = idempotency.replay(db, cu.id, idempotency_key, "POST /profiles")
    if replay is not None:
        return replay
    now = clock.now()
    members = models_accounts.active_members(db, cu.user)
    limit = get_settings().max_profiles_per_account
    if sum(1 for member in members if member.role == "OWNER") >= limit:
        message = f"프로필은 계정당 {limit}개까지 만들 수 있어요."
        raise ApiError(409, "PROFILE_LIMIT_REACHED", message, {"limit": limit})
    # 프로필마다 기존 엔진용 아이 행을 따로 둔다(child_profiles.child_id 가 unique).
    child = Child(
        family_id=cu.user.family_id,
        nickname=req.nickname[:20],
        likes=list(req.interests[:5]),
        want_to_learn=[req.growth_goal] if req.growth_goal else [],
        is_tester=cu.user.is_tester,
    )
    db.add(child)
    db.flush()
    profile = ChildProfile(
        child_id=child.id,
        user_id=cu.id,
        nickname=req.nickname,
        school_or_group=req.school_or_group,
        grade_or_age_band=req.grade_or_age_band,
        interests=list(req.interests),
        growth_goal=req.growth_goal,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    db.flush()
    member = ProfileMember(
        user_id=cu.id,
        profile_id=profile.id,
        child_id=child.id,
        role="OWNER",
        permissions=list(ALL_PERMISSIONS),
        is_default=False,
        created_at=now,
    )
    db.add(member)
    db.flush()
    settings_of(db, profile)
    if req.make_default or not members:
        set_default(db, cu, member)
    body = ProfileResponse(profile=profile_out(profile, member))
    idempotency.remember(db, cu.id, idempotency_key, "POST /profiles", body, status_code=201)
    db.commit()
    return body


# ---------- 조회·수정 ----------


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(
    profile_id: str,
    response: Response,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProfileResponse:
    scope = resolve_scope(db, cu, profile_id, permission="VIEW_PROFILE")
    response.headers["ETag"] = f'"{scope.profile.version}"'
    return ProfileResponse(profile=scope_out(scope))


@router.patch("/{profile_id}", response_model=ProfileResponse)
def update_profile(
    profile_id: str,
    req: ProfileUpdateRequest,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProfileResponse:
    scope = resolve_scope(db, cu, profile_id, permission="MANAGE_DATA")
    profile = scope.profile
    check_version(if_match, req.version, profile.version)
    fields = req.model_dump(exclude_unset=True, exclude={"version", "make_default"})
    if not fields and req.make_default is None:
        raise ApiError(400, "INVALID_INPUT", "바꿀 내용이 없어요.", {"fields": ["body"]})
    for name, value in fields.items():
        setattr(profile, name, list(value) if name == "interests" else value)
    if req.make_default:
        set_default(db, cu, scope.member)
    profile.version += 1
    profile.updated_at = clock.now()
    sync_child_row(scope.child, profile)
    db.commit()
    response.headers["ETag"] = f'"{profile.version}"'
    return ProfileResponse(profile=scope_out(scope))


# ---------- 설정 ----------


@router.get("/{profile_id}/settings", response_model=SettingsResponse)
def get_settings_(
    profile_id: str,
    response: Response,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> SettingsResponse:
    scope = resolve_scope(db, cu, profile_id, permission="VIEW_PROFILE")
    row = settings_of(db, scope.profile)
    db.commit()
    response.headers["ETag"] = f'"{row.version}"'
    return SettingsResponse(settings=settings_out(row, scope.child))


@router.patch("/{profile_id}/settings", response_model=SettingsResponse)
def update_settings(
    profile_id: str,
    req: SettingsUpdateRequest,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> SettingsResponse:
    scope = resolve_scope(db, cu, profile_id, permission="MANAGE_DATA")
    row = settings_of(db, scope.profile)
    check_version(if_match, req.version, row.version, required=False)
    fields = req.model_dump(exclude_unset=True, exclude={"version"})
    if not fields:
        raise ApiError(400, "INVALID_INPUT", "바꿀 내용이 없어요.", {"fields": ["body"]})
    previous_days = row.retention_days
    if "voice_enabled" in fields:
        enabled = fields.pop("voice_enabled")
        if enabled is None:
            raise ApiError(400, "INVALID_INPUT", "음성 사용 여부를 선택해 주세요.", {"fields": ["voiceEnabled"]})
        scope.child.permissions = {**(scope.child.permissions or {}), "voice": enabled}
    for name, value in fields.items():
        setattr(row, name, value)
    row.version += 1
    now = clock.now()
    row.updated_at = now
    notice = _retention_notice(previous_days, row.retention_days, now)
    db.commit()
    response.headers["ETag"] = f'"{row.version}"'
    return SettingsResponse(settings=settings_out(row, scope.child), retention_notice=notice)


def _retention_notice(previous_days: int, days: int, now) -> RetentionNotice | None:
    if previous_days == days:
        return None
    deletes_before = now - timedelta(days=days)
    shorter = days < previous_days
    if shorter:
        message = (
            f"보관 기간을 {previous_days}일에서 {days}일로 줄였어요. "
            f"{iso(deletes_before)} 이전의 대화 기록은 오늘 밤 정리 때 지워집니다. "
            "이야기 책장은 아이가 지울 때까지 그대로 남아요."
        )
    else:
        message = (
            f"보관 기간을 {previous_days}일에서 {days}일로 늘렸어요. 지금 지워지는 기록은 없어요. "
            "앞으로는 이 기간이 지난 대화 기록부터 지워집니다."
        )
    return RetentionNotice(
        previous_days=previous_days,
        retention_days=days,
        effective_at=iso(now) or "",
        deletes_before=iso(deletes_before) or "",
        deletes_now=shorter,
        targets=list(RETENTION_TARGETS),
        message=message,
    )
