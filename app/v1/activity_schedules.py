"""요일별 주제 편성 — 운영자 확인, 조회, 홈 추천 순서 적용.

편성은 아이의 자율 선택을 막지 않는다. 홈 추천의 '순서'만 바꾸고, 추천 이유와 적용 기간을 함께 기록한다.
운영자 판별은 `ADMIN_KAKAO_IDS`(config `# --- v1 activities ---` 블록)와 `auth_identities` 의 계정 식별자를 맞춰 본다.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from .activity_schemas import TopicScheduleOut
from .cursor import iso
from .deps import CurrentUser
from .errors import ApiError
from .models_activity import TopicSchedule
from .models_auth import AuthIdentity


def today_kst() -> date:
    return clock.kst(clock.now()).date()


def require_admin(db: Session, cu: CurrentUser) -> None:
    """운영자 허용 목록에 있는 계정인가. 아니면 403 FORBIDDEN."""
    allowed = get_settings().admin_kakao_ids
    if not allowed:
        raise ApiError(403, "FORBIDDEN", "운영자만 쓸 수 있어요.")
    mine = set(db.scalars(select(AuthIdentity.provider_user_id).where(AuthIdentity.user_id == cu.id)))
    if not mine & set(allowed):
        raise ApiError(403, "FORBIDDEN", "운영자만 쓸 수 있어요.")


def is_active(schedule: TopicSchedule, day: date | None = None) -> bool:
    day = day or today_kst()
    if not (schedule.starts_on <= day <= schedule.ends_on):
        return False
    return schedule.weekday is None or schedule.weekday == day.weekday()


def out(schedule: TopicSchedule, day: date | None = None) -> TopicScheduleOut:
    return TopicScheduleOut(
        id=schedule.id,
        topic_id=schedule.topic_id,
        topic_title=schedule.topic_title,
        category=schedule.category,
        weekday=schedule.weekday,
        starts_on=schedule.starts_on.isoformat(),
        ends_on=schedule.ends_on.isoformat(),
        order=schedule.sort_order,
        reason=schedule.reason,
        active=is_active(schedule, day),
        created_at=iso(schedule.created_at) or "",
        updated_at=iso(schedule.updated_at) or "",
    )


def active_today(db: Session) -> list[TopicSchedule]:
    """오늘(KST) 적용되는 편성. 순서(order) → 만든 시각 순."""
    day = today_kst()
    rows = db.scalars(
        select(TopicSchedule)
        .where(TopicSchedule.starts_on <= day, TopicSchedule.ends_on >= day)
        .order_by(TopicSchedule.sort_order, TopicSchedule.created_at)
    )
    return [s for s in rows if s.weekday is None or s.weekday == day.weekday()]


def order_topic_ids(db: Session) -> list[tuple[str, str]]:
    """오늘 편성된 (주제 id, 추천 이유) 목록. 같은 주제가 여러 번이면 앞의 것만 남긴다."""
    picked: dict[str, str] = {}
    for schedule in active_today(db):
        picked.setdefault(schedule.topic_id, schedule.reason)
    return list(picked.items())


def reorder(db: Session, picks: list[tuple[dict, str]], user_id: str | None = None) -> list[tuple[dict, str]]:
    """편성된 주제를 앞으로 올리고 이유를 편성 문장으로 바꾼다. 개수는 그대로 둔다(자율 선택을 막지 않는다)."""
    planned = order_topic_ids(db)
    if not planned:
        return picks
    from . import topic_catalog  # 순환 import 방지 — 편성이 있을 때만 쓴다

    by_id = {snap["apiId"]: (snap, reason) for snap, reason in picks}
    head: list[tuple[dict, str]] = []
    for topic_id, reason in planned:
        snapshot = by_id.get(topic_id, (None, ""))[0] or topic_catalog.resolve(db, user_id or "", topic_id)
        if snapshot is not None and all(s["apiId"] != topic_id for s, _ in head):
            head.append((snapshot, reason))
    tail = [(s, r) for s, r in picks if all(h["apiId"] != s["apiId"] for h, _ in head)]
    return [*head, *tail][: max(len(picks), 1)]
