"""알림 기록과 푸시 발송(명세 21절).

- 푸시 문구는 **고정 템플릿**이다. 아이의 대화 내용·이야기 제목처럼 민감한 값을 넣지 않는다.
  "확인할 공유 요청이 있어요"처럼 무엇이 있는지만 알리고, 자세한 내용은 앱에서 로그인해 본다.
- 푸시 설정(EXPO_PUSH_URL)이 없으면 발송은 아무것도 하지 않고, 앱 내 알림은 **항상** 기록한다.
- data 에는 화면 이동용 id 만 넣는다. 긴 문자열은 잘라 낸다(실수로 원문이 새는 것을 막는다).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from .models_ops import Notification, NotificationSetting, PushDevice

log = logging.getLogger(__name__)

MAX_DATA_VALUE = 64


@dataclass(frozen=True)
class Template:
    title: str
    body: str
    setting: str  # NotificationSetting 의 어떤 스위치가 이 알림을 끄는가


TEMPLATES: dict[str, Template] = {
    "SHARE_APPROVAL_REQUEST": Template("공유 요청", "확인할 공유 요청이 있어요.", "share_requests"),
    "SHARE_APPROVAL_RESULT": Template("공유 요청 결과", "공유 요청 결과가 나왔어요.", "share_requests"),
    "SAFETY_NOTICE": Template("안전 안내", "확인이 필요한 안내가 있어요.", "safety_notices"),
    "DATA_EXPORT_READY": Template("내 데이터 준비 완료", "내려받을 파일이 준비됐어요.", "push_enabled"),
    "DELETION_SCHEDULED": Template("삭제 예약", "삭제 예정일이 정해졌어요.", "push_enabled"),
    "DELETION_COMPLETED": Template("삭제 완료", "요청한 삭제가 끝났어요.", "push_enabled"),
    "ACTIVITY_SUMMARY": Template("이번 주 기록", "이번 주 이야기가 정리됐어요.", "activity_summary"),
}


def settings_row(db: Session, user_id: str) -> NotificationSetting:
    """없으면 기본값으로 만든다. commit 은 호출자가 한다."""
    row = db.get(NotificationSetting, user_id)
    if row is None:
        row = NotificationSetting(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def _safe_data(data: dict | None) -> dict:
    out: dict[str, str] = {}
    for key, value in (data or {}).items():
        text = str(value)
        if len(text) <= MAX_DATA_VALUE:
            out[str(key)] = text
    return out


def notify(db: Session, user_id: str, kind: str, data: dict | None = None) -> Notification:
    """앱 내 알림을 기록하고(항상), 설정이 허락하면 푸시도 보낸다. commit 은 호출자가 한다."""
    template = TEMPLATES[kind]
    row = Notification(
        user_id=user_id, type=kind, title=template.title, body=template.body, data=_safe_data(data)
    )
    db.add(row)
    db.flush()
    prefs = settings_row(db, user_id)
    if prefs.push_enabled and getattr(prefs, template.setting, True):
        _push(db, user_id, template, row)
    return row


def share_approval_requested(db: Session, user_id: str, share_request_id: str) -> Notification:
    """보호자에게 '확인할 공유 요청이 있어요'. 이야기 제목은 넣지 않는다."""
    return notify(db, user_id, "SHARE_APPROVAL_REQUEST", {"shareRequestId": share_request_id})


def safety_notice(db: Session, user_id: str, event_id: str) -> Notification:
    """안전 안내. 어떤 말이 걸렸는지는 푸시에 넣지 않는다."""
    return notify(db, user_id, "SAFETY_NOTICE", {"safetyEventId": event_id})


def _push(db: Session, user_id: str, template: Template, row: Notification) -> bool:
    """자격 증명이 없으면 아무것도 하지 않는다(앱 내 알림은 이미 기록됐다)."""
    config = get_settings()
    if not config.push_enabled:
        return False
    tokens = list(
        db.scalars(
            select(PushDevice.push_token).where(PushDevice.user_id == user_id, PushDevice.revoked_at.is_(None))
        )
    )
    if not tokens:
        return False
    headers = {"Content-Type": "application/json"}
    if config.expo_access_token:
        headers["Authorization"] = f"Bearer {config.expo_access_token}"
    messages = [
        {
            "to": token,
            "title": template.title,
            "body": template.body,  # 고정 문구. 아이 대화 내용은 절대 넣지 않는다
            "data": {"notificationId": row.id, "type": row.type, **row.data},
        }
        for token in tokens
    ]
    try:
        response = httpx.post(config.expo_push_url, json=messages, headers=headers, timeout=10.0)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — 푸시 실패가 본 기능을 막지 않는다
        log.warning("push send failed: %s", type(exc).__name__)  # 토큰·본문은 남기지 않는다
        return False
    return True
