"""v1 인증 확장 테이블 — 외부 계정 연결, refresh 세션, OAuth state.

모두 새 테이블이다(기존 테이블은 바꾸지 않는다). 토큰·state 원문은 저장하지 않고 SHA-256 해시만 둔다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .models import _now, prefixed_id


class AuthIdentity(Base):
    """외부 로그인 계정 ↔ JJCP 사용자. (provider, provider_user_id) 하나에 사용자 한 명."""

    __tablename__ = "auth_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id", name="uq_auth_identity_provider_user"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: prefixed_id("aid"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(12))  # KAKAO|DEV
    # KAKAO: 카카오 회원번호. DEV: sha256(deviceKey).
    provider_user_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_login_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RefreshSession(Base):
    """refresh token 한 개 = 한 행. 회전하면 같은 chain_id 로 새 행을 만들고 이전 행에 rotated_at 을 찍는다.

    chain_id 는 로그인 한 번으로 시작한 세션 계열 id 이고, access_tokens.refresh_session_id 에 이 값을 넣는다.
    이미 회전된 토큰이 다시 오면 chain 전체(refresh·access)를 폐기한다.
    """

    __tablename__ = "refresh_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: prefixed_id("rft"))
    chain_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    platform: Mapped[str] = mapped_column(String(12))  # WEB|ANDROID|IOS|DEV
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class OAuthState(Base):
    """웹 로그인 state(10분, 1회용). PKCE code_verifier 는 토큰 교환에 원문이 필요해 그대로 둔다."""

    __tablename__ = "oauth_states"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: prefixed_id("ost"))
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(12), default="KAKAO")
    return_to: Mapped[str] = mapped_column(String(512), default="/")
    code_verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
