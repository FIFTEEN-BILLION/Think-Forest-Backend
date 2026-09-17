"""첫인사 완료 여부. 대화 작업에서 child_profiles 기준으로 바꾼다."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Child
from .models import User


def needs_first_greeting(db: Session, user: User) -> bool:
    child = db.get(Child, user.child_id)
    return not (child and child.profile_confirmed)
