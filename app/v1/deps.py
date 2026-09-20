"""v1 인증 의존성. 모든 v1 조회·변경은 로그인 사용자와 세션 소유 관계를 서버에서 확인한다."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import hash_token
from ..db import get_session
from ..models import Child
from . import guests, models_accounts, permissions
from .errors import ApiError
from .models import AccessToken, User
from .models_accounts import ProfileMember
from .models_conversation import ChildProfile


@dataclass
class CurrentUser:
    user: User
    child: Child

    @property
    def id(self) -> str:
        return self.user.id


def require_user(
    request: Request, authorization: str | None = Header(default=None), db: Session = Depends(get_session)
) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "UNAUTHORIZED", "다시 로그인해 주세요.")
    raw = authorization.removeprefix("Bearer ").strip()
    token = db.scalar(select(AccessToken).where(AccessToken.token_hash == hash_token(raw)))
    if token is None or token.revoked_at is not None or token.expires_at <= clock.now():
        raise ApiError(401, "UNAUTHORIZED", "다시 로그인해 주세요.")
    user = db.get(User, token.user_id)
    child = db.get(Child, user.child_id) if user else None
    if user is None or child is None or user.status != "ACTIVE":
        raise ApiError(401, "UNAUTHORIZED", "다시 로그인해 주세요.")
    if user.role == "GUEST":
        guests.check_access(db, user, request.scope["route"].path, request.method)
    return CurrentUser(user=user, child=child)


@dataclass
class ProfileScope:
    """아이 프로필 단위 접근 범위. URL 의 profileId 를 그대로 믿지 않고 연결 관계로 확인한다."""

    user: User
    child: Child
    profile: ChildProfile
    permissions: tuple[str, ...]
    member: ProfileMember | None = None

    @property
    def profile_id(self) -> str:
        return self.profile.id

    @property
    def role(self) -> str:
        return self.member.role if self.member else "OWNER"

    def has(self, permission: str) -> bool:
        return permissions.has(self.permissions, permission)

    def require(self, permission: str) -> None:
        if not self.has(permission):
            message = "보호자 권한이 없어요."
            raise ApiError(403, "FORBIDDEN", message, {"profileId": self.profile.id, "required": permission})


def _scope_from_member(db: Session, cu: CurrentUser, member: ProfileMember) -> ProfileScope | None:
    profile = db.get(ChildProfile, member.profile_id)
    child = db.get(Child, member.child_id)
    if profile is None or child is None:
        return None
    return ProfileScope(
        user=cu.user,
        child=child,
        profile=profile,
        permissions=tuple(member.permissions or ()),
        member=member,
    )


def _resolve_profile(db: Session, cu: CurrentUser, profile_id: str | None) -> ProfileScope | None:
    """계정이 볼 수 있는 프로필(내 프로필 또는 보호자 연결로 붙은 프로필)을 그 연결의 권한과 함께 찾는다.

    profileId 를 주지 않으면 이 계정의 기본 프로필을 쓴다(기존 한 계정 = 한 아이 동작 유지).
    """
    member = (
        models_accounts.member_for(db, cu.user, profile_id)
        if profile_id
        else models_accounts.default_member(db, cu.user)
    )
    return _scope_from_member(db, cu, member) if member else None


def resolve_scope(
    db: Session, cu: CurrentUser, profile_id: str | None = None, *, permission: str | None = None
) -> ProfileScope:
    """경로 파라미터로 받은 profileId 용. 남의 프로필은 존재 자체를 알리지 않는다(404)."""
    scope = _resolve_profile(db, cu, profile_id)
    if scope is None:
        raise ApiError(404, "PROFILE_NOT_FOUND", "아이 프로필을 찾을 수 없어요.", {"profileId": profile_id})
    if permission:
        scope.require(permission)
    return scope


def require_profile(
    profile_id: str | None = Query(default=None, alias="profileId"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProfileScope:
    return resolve_scope(db, cu, profile_id)


def optional_profile(
    profile_id: str | None = Query(default=None, alias="profileId"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProfileScope | None:
    return _resolve_profile(db, cu, profile_id)


def require_consents(db: Session, scope: ProfileScope, documents: tuple[str, ...]) -> None:
    """명세 26절 — 동의가 꼭 있어야 하는 요청. 대화 API 는 여기 걸지 않는다(동의가 없으면 규칙 기반으로 계속한다)."""
    missing = models_accounts.missing_consents(db, scope.profile_id, documents)
    if missing:
        raise ApiError(
            403,
            "CONSENT_REQUIRED",
            "보호자 동의가 필요해요.",
            {"profileId": scope.profile_id, "documentIds": missing},
        )
