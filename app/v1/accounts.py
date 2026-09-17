"""v1 계정 생성과 access token 발급(인증·대화 작업이 함께 쓴다)."""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from .. import clock
from ..auth import hash_token
from ..models import Child, Family
from .models import AccessToken, User

ACCESS_TTL_SECONDS = 3600


def create_account(db: Session, *, role: str = "CHILD", is_tester: bool = False, nickname: str = "") -> User:
    """사용자 + 기존 대화 엔진용 가족·아이 레코드를 함께 만든다. commit 은 호출자가 한다."""
    family = Family(guardian_token_hash=hash_token(f"v1_{secrets.token_urlsafe(24)}"))
    db.add(family)
    db.flush()
    child = Child(family_id=family.id, nickname=nickname[:20], is_tester=is_tester)
    db.add(child)
    db.flush()
    user = User(role=role, family_id=family.id, child_id=child.id, is_tester=is_tester)
    db.add(user)
    db.flush()
    return user


def issue_access_token(
    db: Session, user: User, *, refresh_session_id: str | None = None, ttl: int = ACCESS_TTL_SECONDS
) -> str:
    raw = f"jat_{secrets.token_urlsafe(32)}"
    db.add(
        AccessToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            refresh_session_id=refresh_session_id,
            expires_at=clock.now() + timedelta(seconds=ttl),
        )
    )
    db.flush()
    return raw
