"""v1 알림과 기기(명세 21절).

- `POST /devices`, `DELETE /devices/{deviceId}` — 푸시 토큰 등록·갱신·해제(Expo push token)
- `GET|PATCH /notification-settings` — 공유 요청·안전 안내 수신 설정
- `GET /notifications`, `POST /notifications/{id}/read` — 앱 내 알림 목록과 읽음 처리

푸시 본문은 `app/v1/ops_notify.py` 의 고정 템플릿만 쓴다. 아이의 대화 내용은 푸시에 넣지 않는다.
푸시 토큰은 응답·로그에 싣지 않는다.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from ...schemas.common import CamelModel
from .. import cursor, ops_notify
from ..deps import CurrentUser, require_user
from ..errors import ApiError
from ..models_ops import Notification, PushDevice

router = APIRouter(tags=["v1-notifications"])

Platform = Literal["IOS", "ANDROID", "WEB"]
# Expo push token. 웹 푸시는 아직 쓰지 않으므로 IOS·ANDROID 만 형식을 확인한다.
EXPO_TOKEN = re.compile(r"^Expo(nent)?PushToken\[[A-Za-z0-9_\-]{1,120}\]$")


class DeviceRequest(CamelModel):
    platform: Platform
    push_token: str = Field(min_length=1, max_length=200)
    installation_id: str | None = Field(default=None, max_length=64)
    app_version: str | None = Field(default=None, max_length=20)
    locale: str = Field(default="ko-KR", max_length=16)


class Device(CamelModel):
    id: str
    platform: Platform
    installation_id: str | None = None
    app_version: str | None = None
    created_at: str
    updated_at: str


class DeviceResponse(CamelModel):
    device: Device


class OkResponse(CamelModel):
    ok: bool = True


class NotificationSettings(CamelModel):
    push_enabled: bool
    share_requests: bool
    safety_notices: bool
    activity_summary: bool
    updated_at: str


class NotificationSettingsResponse(CamelModel):
    settings: NotificationSettings


class NotificationSettingsPatch(CamelModel):
    push_enabled: bool | None = None
    share_requests: bool | None = None
    safety_notices: bool | None = None
    activity_summary: bool | None = None


class NotificationItem(CamelModel):
    id: str
    type: str
    title: str
    body: str
    data: dict
    read_at: str | None = None
    created_at: str


class NotificationList(CamelModel):
    items: list[NotificationItem]
    unread_count: int
    next_cursor: str | None = None


class NotificationResponse(CamelModel):
    notification: NotificationItem


def _device_out(row: PushDevice) -> Device:
    return Device(
        id=row.id,
        platform=row.platform,
        installation_id=row.installation_id,
        app_version=row.app_version,
        created_at=cursor.iso(row.created_at) or "",
        updated_at=cursor.iso(row.updated_at) or "",
    )


@router.post("/devices", response_model=DeviceResponse, status_code=201)
def register_device(
    body: DeviceRequest, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> DeviceResponse:
    """같은 토큰을 다시 보내면 갱신한다(등록은 여러 번 불러도 안전하다)."""
    if body.platform in ("IOS", "ANDROID") and not EXPO_TOKEN.match(body.push_token):
        raise ApiError(400, "INVALID_INPUT", "푸시 토큰 형식을 확인해 주세요.", {"fields": ["pushToken"]})
    row = db.scalar(
        select(PushDevice).where(PushDevice.user_id == cu.id, PushDevice.push_token == body.push_token)
    )
    if row is None:
        row = PushDevice(user_id=cu.id, push_token=body.push_token, platform=body.platform)
        db.add(row)
    row.platform = body.platform
    row.installation_id = body.installation_id
    row.app_version = body.app_version
    row.locale = body.locale
    row.revoked_at = None
    row.updated_at = clock.now()
    ops_notify.settings_row(db, cu.id)
    db.commit()
    return DeviceResponse(device=_device_out(row))


@router.delete("/devices/{device_id}", response_model=OkResponse)
def unregister_device(
    device_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> OkResponse:
    row = db.get(PushDevice, device_id)
    if row is None or row.user_id != cu.id:
        raise ApiError(404, "DEVICE_NOT_FOUND", "등록된 기기를 찾을 수 없어요.")
    db.delete(row)
    db.commit()
    return OkResponse()


def _settings_out(row) -> NotificationSettings:
    return NotificationSettings(
        push_enabled=row.push_enabled,
        share_requests=row.share_requests,
        safety_notices=row.safety_notices,
        activity_summary=row.activity_summary,
        updated_at=cursor.iso(row.updated_at) or "",
    )


@router.get("/notification-settings", response_model=NotificationSettingsResponse)
def get_notification_settings(
    cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> NotificationSettingsResponse:
    row = ops_notify.settings_row(db, cu.id)
    db.commit()
    return NotificationSettingsResponse(settings=_settings_out(row))


@router.patch("/notification-settings", response_model=NotificationSettingsResponse)
def update_notification_settings(
    body: NotificationSettingsPatch, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> NotificationSettingsResponse:
    row = ops_notify.settings_row(db, cu.id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(row, field, value)
    row.updated_at = clock.now()
    db.commit()
    return NotificationSettingsResponse(settings=_settings_out(row))


def _notification_out(row: Notification) -> NotificationItem:
    return NotificationItem(
        id=row.id,
        type=row.type,
        title=row.title,
        body=row.body,
        data=row.data or {},
        read_at=cursor.iso(row.read_at),
        created_at=cursor.iso(row.created_at) or "",
    )


@router.get("/notifications", response_model=NotificationList)
def list_notifications(
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> NotificationList:
    size = cursor.clamp_limit(limit)
    stmt = select(Notification).where(Notification.user_id == cu.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(
            or_(Notification.created_at < at, and_(Notification.created_at == at, Notification.id < row_id))
        )
    rows = list(db.scalars(stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(size + 1)))
    page = rows[:size]
    unread = db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == cu.id, Notification.read_at.is_(None))
    )
    return NotificationList(
        items=[_notification_out(row) for row in page],
        unread_count=int(unread or 0),
        next_cursor=cursor.encode(page[-1].created_at, page[-1].id) if len(rows) > size else None,
    )


@router.post("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_read(
    notification_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
) -> NotificationResponse:
    """여러 번 불러도 처음 읽은 시각이 그대로다."""
    row = db.get(Notification, notification_id)
    if row is None or row.user_id != cu.id:
        raise ApiError(404, "NOTIFICATION_NOT_FOUND", "알림을 찾을 수 없어요.")
    if row.read_at is None:
        row.read_at = clock.now()
        db.commit()
    return NotificationResponse(notification=_notification_out(row))
