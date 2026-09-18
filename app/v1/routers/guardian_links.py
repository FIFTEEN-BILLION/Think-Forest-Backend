"""v1 보호자 연결 — 초대 만들기·받기, 연결한 아이 목록, 권한 변경, 연결 해제.

- 초대 토큰은 1회용이고 짧게 산다(기본 30분). 원문은 저장하지 않고 SHA-256 해시만 둔다.
- 연결 해제는 **연결만** 끊는다. 아이 기록은 지우지 않는다(지우기는 내 데이터 API 가 따로 한다).
- 초대를 만들려면 아이 개인정보 동의(`privacy_child`)가 있어야 한다 — 없으면 403 CONSENT_REQUIRED.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...auth import hash_token
from ...config import get_settings
from ...db import get_session
from .. import cursor, models_accounts, permissions
from ..cursor import iso
from ..deps import CurrentUser, require_consents, require_user, resolve_scope
from ..errors import ApiError
from ..models_accounts import GuardianInvitation, ProfileMember
from ..models_conversation import ChildProfile
from ..schemas_accounts import (
    GuardianChildListResponse,
    GuardianChildOut,
    InvitationAcceptResponse,
    InvitationCreateRequest,
    InvitationOut,
    InvitationResponse,
    LinkListResponse,
    LinkOut,
    LinkResponse,
    LinkUpdateRequest,
    UnlinkResponse,
)
from .profiles import profile_out

router = APIRouter(tags=["v1-guardian-links"])

INVITE_DOCUMENTS = ("privacy_child",)


def link_out(member: ProfileMember) -> LinkOut:
    return LinkOut(
        id=member.id,
        profile_id=member.profile_id,
        user_id=member.user_id,
        role=member.role,
        permissions=list(member.permissions or []),
        status="REVOKED" if member.revoked_at else "ACTIVE",
        created_at=iso(member.created_at) or "",
        revoked_at=iso(member.revoked_at),
    )


def _link_or_404(db: Session, cu: CurrentUser, link_id: str, *, allow_self: bool = False) -> ProfileMember:
    """내가 OWNER 인 프로필의 연결만 만질 수 있다(내 연결을 스스로 끊는 것은 허용). 남의 것은 404."""
    member = db.get(ProfileMember, link_id)
    if member is None:
        raise ApiError(404, "LINK_NOT_FOUND", "연결을 찾을 수 없어요.", {"linkId": link_id})
    if allow_self and member.user_id == cu.id:
        return member
    mine = models_accounts.member_for(db, cu.user, member.profile_id)
    if mine is None or mine.role != "OWNER":
        raise ApiError(404, "LINK_NOT_FOUND", "연결을 찾을 수 없어요.", {"linkId": link_id})
    return member


# ---------- 초대 ----------


@router.post("/guardian-links/invitations", response_model=InvitationResponse, status_code=201)
def create_invitation(
    req: InvitationCreateRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> InvitationResponse:
    scope = resolve_scope(db, cu, req.profile_id, permission="MANAGE_DATA")
    require_consents(db, scope, INVITE_DOCUMENTS)
    granted = permissions.normalize(req.permissions)
    minutes = req.expires_in_minutes or get_settings().guardian_invite_ttl_minutes
    now = clock.now()
    token = f"ginv_{secrets.token_urlsafe(24)}"
    invitation = GuardianInvitation(
        profile_id=scope.profile_id,
        inviter_user_id=cu.id,
        token_hash=hash_token(token),
        permissions=granted,
        expires_at=now + timedelta(minutes=minutes),
        created_at=now,
    )
    db.add(invitation)
    db.commit()
    return InvitationResponse(
        invitation=InvitationOut(
            id=invitation.id,
            profile_id=invitation.profile_id,
            token=token,
            permissions=granted,
            expires_at=iso(invitation.expires_at) or "",
            created_at=iso(invitation.created_at) or "",
        )
    )


@router.post("/guardian-links/invitations/{token}/accept", response_model=InvitationAcceptResponse)
def accept_invitation(
    token: str,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> InvitationAcceptResponse:
    invitation = db.scalar(select(GuardianInvitation).where(GuardianInvitation.token_hash == hash_token(token)))
    if invitation is None:
        raise ApiError(404, "INVITATION_NOT_FOUND", "초대를 찾을 수 없어요.")
    now = clock.now()
    if invitation.used_at is not None:
        raise ApiError(409, "INVITATION_ALREADY_USED", "이미 쓴 초대예요. 새 초대를 받아 주세요.")
    if invitation.expires_at <= now:
        raise ApiError(409, "INVITATION_EXPIRED", "초대 시간이 지났어요. 새 초대를 받아 주세요.")
    if models_accounts.member_for(db, cu.user, invitation.profile_id) is not None:
        raise ApiError(409, "ALREADY_LINKED", "이미 연결된 아이예요.", {"profileId": invitation.profile_id})
    profile = db.get(ChildProfile, invitation.profile_id)
    if profile is None:
        raise ApiError(404, "PROFILE_NOT_FOUND", "아이 프로필을 찾을 수 없어요.")

    invitation.used_at = now
    invitation.accepted_user_id = cu.id
    member = ProfileMember(
        user_id=cu.id,
        profile_id=profile.id,
        child_id=profile.child_id,
        role="GUARDIAN",
        permissions=list(invitation.permissions or []),
        is_default=False,
        created_at=now,
    )
    db.add(member)
    db.commit()
    return InvitationAcceptResponse(link=link_out(member), profile=profile_out(profile, member))


# ---------- 연결한 아이·연결 목록 ----------


@router.get("/guardian/children", response_model=GuardianChildListResponse)
def guardian_children(
    limit: int | None = Query(default=None, ge=1, le=50),
    page_cursor: str | None = Query(default=None, alias="cursor"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> GuardianChildListResponse:
    members = models_accounts.active_members(db, cu.user)
    size = cursor.clamp_limit(limit)
    offset = cursor.decode_offset(page_cursor)
    items = []
    for member in members[offset : offset + size]:
        profile = db.get(ChildProfile, member.profile_id)
        if profile is None:
            continue
        items.append(
            GuardianChildOut(
                profile_id=profile.id,
                link_id=member.id,
                nickname=profile.nickname,
                grade_or_age_band=profile.grade_or_age_band,
                role=member.role,
                permissions=list(member.permissions or []),
                is_default=member.is_default,
                needs_first_greeting=profile.completed_at is None,
                updated_at=iso(profile.updated_at) or "",
            )
        )
    has_more = offset + size < len(members)
    return GuardianChildListResponse(
        items=items, next_cursor=cursor.encode_offset(offset + size) if has_more else None
    )


@router.get("/guardian-links", response_model=LinkListResponse)
def list_links(
    profile_id: str | None = Query(default=None, alias="profileId"),
    limit: int | None = Query(default=None, ge=1, le=50),
    page_cursor: str | None = Query(default=None, alias="cursor"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> LinkListResponse:
    """내가 OWNER 인 프로필에 붙은 연결을 모두, 초대로 붙은 프로필은 내 연결만 보여 준다."""
    mine = models_accounts.active_members(db, cu.user)
    if profile_id:
        mine = [member for member in mine if member.profile_id == profile_id]
        if not mine:
            raise ApiError(404, "PROFILE_NOT_FOUND", "아이 프로필을 찾을 수 없어요.", {"profileId": profile_id})
    owned = [member.profile_id for member in mine if member.role == "OWNER"]
    rows: list[ProfileMember] = []
    if owned:
        rows = list(
            db.scalars(
                select(ProfileMember)
                .where(ProfileMember.profile_id.in_(owned), ProfileMember.revoked_at.is_(None))
                .order_by(ProfileMember.created_at, ProfileMember.id)
            )
        )
    rows += [member for member in mine if member.role != "OWNER"]
    size = cursor.clamp_limit(limit)
    offset = cursor.decode_offset(page_cursor)
    has_more = offset + size < len(rows)
    return LinkListResponse(
        items=[link_out(member) for member in rows[offset : offset + size]],
        next_cursor=cursor.encode_offset(offset + size) if has_more else None,
    )


# ---------- 권한 변경·연결 해제 ----------


@router.patch("/guardian-links/{link_id}", response_model=LinkResponse)
def update_link(
    link_id: str,
    req: LinkUpdateRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> LinkResponse:
    member = _link_or_404(db, cu, link_id)
    if member.role == "OWNER":
        raise ApiError(403, "FORBIDDEN", "프로필을 만든 계정의 권한은 바꿀 수 없어요.", {"linkId": link_id})
    if member.revoked_at is not None:
        raise ApiError(409, "LINK_REVOKED", "이미 해제된 연결이에요.", {"linkId": link_id})
    member.permissions = permissions.normalize(list(req.permissions))
    db.commit()
    return LinkResponse(link=link_out(member))


@router.delete("/guardian-links/{link_id}", response_model=UnlinkResponse)
def delete_link(
    link_id: str,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> UnlinkResponse:
    member = _link_or_404(db, cu, link_id, allow_self=True)
    if member.role == "OWNER":
        raise ApiError(403, "FORBIDDEN", "프로필을 만든 계정의 연결은 해제할 수 없어요.", {"linkId": link_id})
    if member.revoked_at is None:
        member.revoked_at = clock.now()
        db.commit()
    return UnlinkResponse(link_id=link_id)
