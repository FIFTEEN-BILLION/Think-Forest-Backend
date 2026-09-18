"""v1 계정 기본 테이블 — 사용자와 access token.

운영 DB 에 마이그레이션 도구가 없으므로 기존 테이블은 바꾸지 않고 새 테이블만 더한다(create_all 은 새 테이블만 만든다).
사용자 한 명은 기존 대화 엔진을 재사용하려고 가족(families)·아이(children) 레코드와 1:1 로 연결된다.
토큰 원문은 저장하지 않고 SHA-256 해시만 둔다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .. import clock
from ..db import Base


def prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> datetime:
    return clock.now()


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: prefixed_id("usr"))
    role: Mapped[str] = mapped_column(String(12), default="CHILD")  # CHILD|GUARDIAN
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")  # ACTIVE|DELETED
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    # 개발용 로그인으로 만든 성인 테스터. demo 모드에서도 실제 AI 를 쓸 수 있다.
    is_tester: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AccessToken(Base):
    __tablename__ = "access_tokens"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: prefixed_id("atk"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    refresh_session_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
