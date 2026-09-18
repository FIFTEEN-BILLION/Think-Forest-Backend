"""음성 WebSocket 1회용 접속권(명세 20.1).

브라우저 WebSocket 은 Authorization 헤더를 넣기 어렵고 access token 을 URL 에 두면 안 된다.
그래서 짧게(기본 30초) 살고 한 번만 쓸 수 있는 ticket 을 먼저 발급한다.
ticket 원문은 저장하지 않고 SHA-256 해시만 둔다. 로그에도 남기지 않는다.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import hash_token
from ..config import get_settings
from .models import User
from .models_ops import SpeechStreamTicket

TICKET_PREFIX = "stk_"


class TicketInvalid(Exception):
    """없거나·만료됐거나·이미 쓴 ticket. 새 ticket 을 받아 다시 연결한다."""


def issue(
    db: Session,
    user: User,
    *,
    profile_id: str | None,
    conversation_id: str | None,
    question_id: str | None,
    locale: str,
    sample_rate: int,
) -> tuple[SpeechStreamTicket, str]:
    """(행, ticket 원문) — 원문은 이 응답에서만 나가고 다시 볼 수 없다."""
    raw = f"{TICKET_PREFIX}{secrets.token_urlsafe(24)}"
    ttl = get_settings().speech_ticket_ttl_seconds
    row = SpeechStreamTicket(
        user_id=user.id,
        profile_id=profile_id,
        conversation_id=conversation_id,
        question_id=question_id,
        locale=locale,
        sample_rate=sample_rate,
        ticket_hash=hash_token(raw),
        expires_at=clock.now() + timedelta(seconds=ttl),
    )
    db.add(row)
    db.flush()
    return row, raw


def consume(db: Session, raw: str | None) -> SpeechStreamTicket:
    """접속 순간에 1회용으로 소비한다. 같은 ticket 으로 다시 연결할 수 없다."""
    if not raw or not raw.startswith(TICKET_PREFIX):
        raise TicketInvalid
    row = db.scalar(select(SpeechStreamTicket).where(SpeechStreamTicket.ticket_hash == hash_token(raw)))
    now = clock.now()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise TicketInvalid
    row.used_at = now
    db.commit()
    return row


def websocket_url(base_url: str) -> str:
    """설정의 공개 WS 주소, 없으면 요청 주소에서 ws/wss 를 유도한다."""
    configured = get_settings().public_ws_base_url
    if configured:
        return f"{configured}/api/v1/speech/stream"
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/api/v1/speech/stream"
