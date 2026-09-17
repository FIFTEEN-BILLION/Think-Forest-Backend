"""첫인사 완료 여부 — 이 사용자 아이의 child_profiles 에 완료된 프로필이 있는지로 판단한다."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User
from .models_conversation import ChildProfile


def completed_profile(db: Session, user: User) -> ChildProfile | None:
    return db.scalar(
        select(ChildProfile).where(ChildProfile.child_id == user.child_id, ChildProfile.completed_at.is_not(None))
    )


def needs_first_greeting(db: Session, user: User) -> bool:
    return completed_profile(db, user) is None
