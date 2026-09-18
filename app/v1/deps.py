"""v1 인증 의존성. 모든 v1 조회·변경은 로그인 사용자와 세션 소유 관계를 서버에서 확인한다."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import hash_token
from ..db import get_session
from ..models import Child
from .errors import ApiError
from .models import AccessToken, User
from .models_conversation import ChildProfile
from .permissions import ALL_PERMISSIONS


@dataclass
class CurrentUser:
    user: User
    child: Child

    @property
    def id(self) -> str:
        return self.user.id


def require_user(authorization: str | None = Header(default=None), db: Session = Depends(get_session)) -> CurrentUser:
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
    return CurrentUser(user=user, child=child)


@dataclass
class ProfileScope:
    """아이 프로필 단위 접근 범위. URL 의 profileId 를 그대로 믿지 않고 연결 관계로 확인한다."""

    user: User
    child: Child
    profile: ChildProfile
    permissions: tuple[str, ...]

    @property
    def profile_id(self) -> str:
        return self.profile.id


def _resolve_profile(db: Session, cu: CurrentUser, profile_id: str | None) -> ChildProfile | None:
    """지금은 계정마다 아이 프로필 하나다. B1 트랙이 여러 프로필·보호자 연결로 넓힌다."""
    own = db.scalar(select(ChildProfile).where(ChildProfile.child_id == cu.child.id))
    if profile_id and (own is None or own.id != profile_id):
        return None
    return own


def require_profile(
    profile_id: str | None = Query(default=None, alias="profileId"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProfileScope:
    profile = _resolve_profile(db, cu, profile_id)
    if profile is None:
        raise ApiError(404, "PROFILE_NOT_FOUND", "아이 프로필을 찾을 수 없어요.", {"profileId": profile_id})
    return ProfileScope(user=cu.user, child=cu.child, profile=profile, permissions=ALL_PERMISSIONS)


def optional_profile(
    profile_id: str | None = Query(default=None, alias="profileId"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ProfileScope | None:
    profile = _resolve_profile(db, cu, profile_id)
    return ProfileScope(user=cu.user, child=cu.child, profile=profile, permissions=ALL_PERMISSIONS) if profile else None
