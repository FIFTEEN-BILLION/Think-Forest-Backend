"""v1 요일별 주제 운영 — 운영자만 쓰는 추천 편성 API.

편성은 아이의 자율 선택을 막지 않는다. `/home` 추천 순서에만 영향을 주고 추천 이유와 적용 기간을 남긴다.
운영자 판별은 `ADMIN_KAKAO_IDS` 허용 목록이다(설정은 config 의 `# --- v1 activities ---` 블록).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import activity_schedules as schedules
from .. import topic_catalog
from ..activity_schemas import (
    TopicScheduleCreateRequest,
    TopicScheduleList,
    TopicScheduleResponse,
    TopicScheduleUpdateRequest,
)
from ..deps import CurrentUser, require_user
from ..errors import ApiError
from ..models_activity import TopicSchedule

router = APIRouter(prefix="/admin/topic-schedules", tags=["v1-admin-topics"])


def _own(db: Session, schedule_id: str) -> TopicSchedule:
    schedule = db.get(TopicSchedule, schedule_id)
    if schedule is None:
        raise ApiError(404, "SCHEDULE_NOT_FOUND", "편성을 찾을 수 없어요.", {"scheduleId": schedule_id})
    return schedule


@router.get("", response_model=TopicScheduleList)
def list_topic_schedules(
    weekday: int | None = Query(default=None, ge=0, le=6),
    active: bool | None = Query(default=None, description="true 면 오늘(KST) 적용되는 편성만"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    schedules.require_admin(db, cu)
    rows = list(
        db.scalars(
            select(TopicSchedule).order_by(
                TopicSchedule.starts_on, TopicSchedule.sort_order, TopicSchedule.created_at
            )
        )
    )
    if weekday is not None:
        rows = [r for r in rows if r.weekday in (None, weekday)]
    if active is not None:
        rows = [r for r in rows if schedules.is_active(r) is active]
    return TopicScheduleList(items=[schedules.out(r) for r in rows])


@router.post("", response_model=TopicScheduleResponse, status_code=201)
def create_topic_schedule(
    req: TopicScheduleCreateRequest, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
):
    schedules.require_admin(db, cu)
    snapshot = topic_catalog.resolve(db, cu.id, req.topic_id)
    if snapshot is None or snapshot.get("source") == "user":
        raise ApiError(404, "TOPIC_NOT_FOUND", "편성할 수 있는 주제가 아니에요.", {"topicId": req.topic_id})
    now = clock.now()
    schedule = TopicSchedule(
        topic_id=snapshot["apiId"],
        topic_title=snapshot["title"][:80],
        category=snapshot["apiCategory"],
        weekday=req.weekday,
        starts_on=req.starts_on,
        ends_on=req.ends_on,
        sort_order=req.order,
        reason=" ".join(req.reason.split()),
        created_by=cu.id,
        created_at=now,
        updated_at=now,
    )
    db.add(schedule)
    db.commit()
    return TopicScheduleResponse(schedule=schedules.out(schedule))


@router.patch("/{schedule_id}", response_model=TopicScheduleResponse)
def update_topic_schedule(
    schedule_id: str,
    req: TopicScheduleUpdateRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    schedules.require_admin(db, cu)
    schedule = _own(db, schedule_id)
    if req.starts_on is not None:
        schedule.starts_on = req.starts_on
    if req.ends_on is not None:
        schedule.ends_on = req.ends_on
    if req.clear_weekday:
        schedule.weekday = None
    elif req.weekday is not None:
        schedule.weekday = req.weekday
    if req.order is not None:
        schedule.sort_order = req.order
    if req.reason is not None:
        schedule.reason = " ".join(req.reason.split())
    if schedule.ends_on < schedule.starts_on:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["endsOn"]})
    schedule.updated_at = clock.now()
    db.commit()
    return TopicScheduleResponse(schedule=schedules.out(schedule))


@router.delete("/{schedule_id}", status_code=204)
def delete_topic_schedule(
    schedule_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
):
    schedules.require_admin(db, cu)
    db.delete(_own(db, schedule_id))
    db.commit()
    return Response(status_code=204)
