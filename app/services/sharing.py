"""공유 이야기의 보이는 범위. 게시된 것만, 가족/모임/전체 범위에 맞춰 보인다."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CircleMember, SharedItem


def circle_ids(db: Session, family_id: str) -> list[str]:
    return list(db.scalars(select(CircleMember.circle_id).where(CircleMember.family_id == family_id)))


def is_visible(item: SharedItem, family_id: str, circles: list[str]) -> bool:
    if item.status != "published":
        return False
    if item.visibility == "family":
        return item.family_id == family_id
    if item.visibility == "circle":
        return item.circle_id in circles
    return item.visibility == "community"
