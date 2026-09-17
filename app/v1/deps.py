"""v1 인증 의존성. 모든 v1 조회·변경은 로그인 사용자와 세션 소유 관계를 서버에서 확인한다."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import hash_token
from ..db import get_session
from ..models import Child
from .errors import ApiError
from .models import AccessToken, User


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
