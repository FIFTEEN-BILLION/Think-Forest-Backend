"""v1 운영 테이블 — 음성 스트림 접속권, 푸시 기기, 알림 설정·알림, 내 데이터 작업, 삭제 요청.

- 모두 새 테이블이다. 기존 테이블 스키마는 바꾸지 않는다(운영 DB 에 마이그레이션 도구가 없다).
- 음성: 접속권은 해시로만 저장하고, 음성 조각과 중간 자막은 어디에도 저장하지 않는다.
- 알림: 본문에는 아이 대화 내용을 넣지 않는다. "확인할 공유 요청이 있어요" 같은 고정 문구만 둔다.
- 삭제 요청 행이 곧 감사 기록이다. 요청자·대상 ID·시각·처리 결과(개수)만 남기고 아이 원문은 남기지 않는다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .models import _now, prefixed_id


class SpeechStreamTicket(Base):
    """`POST /speech/stream-tickets` 가 만드는 1회용 WebSocket 접속권(기본 30초).

    id 가 곧 streamId 다. ticket 원문은 저장하지 않고 SHA-256 해시만 둔다.
    """

    __tablename__ = "speech_stream_tickets"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("sts"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locale: Mapped[str] = mapped_column(String(16), default="ko-KR")
    sample_rate: Mapped[int] = mapped_column(Integer, default=16000)
    ticket_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PushDevice(Base):
    """푸시를 받을 기기. Expo push token 은 보낼 때 필요해 원문을 두되 로그·응답에는 싣지 않는다."""

    __tablename__ = "push_devices"
    __table_args__ = (UniqueConstraint("user_id", "push_token"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("dev"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    platform: Mapped[str] = mapped_column(String(10))  # IOS|ANDROID|WEB
    push_token: Mapped[str] = mapped_column(String(200))
    installation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    locale: Mapped[str] = mapped_column(String(16), default="ko-KR")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class NotificationSetting(Base):
    """사용자 한 명에 한 줄. 없으면 기본값으로 만들어 준다."""

    __tablename__ = "notification_settings"

    user_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    share_requests: Mapped[bool] = mapped_column(Boolean, default=True)
    safety_notices: Mapped[bool] = mapped_column(Boolean, default=True)
    activity_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Notification(Base):
    """앱 안에서 보여 줄 알림. 푸시를 못 보내도 이 기록은 남긴다."""

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("ntf"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(String(160))  # 고정 문구만. 아이 대화 내용 금지
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # 화면 이동용 id 만
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DataJob(Base):
    """내 데이터 내보내기 작업. 파일 시스템을 쓰지 않고 결과 JSON 을 payload 에 둔다."""

    __tablename__ = "data_jobs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("job"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    type: Mapped[str] = mapped_column(String(20), default="DATA_EXPORT")
    status: Mapped[str] = mapped_column(String(12), default="QUEUED")
    format: Mapped[str] = mapped_column(String(10), default="JSON")
    include: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    # 다운로드 토큰도 해시로만 둔다. 한 번 쓰면 지운다.
    download_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    download_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeletionRequest(Base):
    """아이 데이터 삭제(DATA, `del_`)와 계정 탈퇴(ACCOUNT, `acc_`) 요청 겸 감사 기록.

    유예기간(effective_at)이 지나면 실제로 지우고, 이 행만 남긴다. 지운 본문은 남기지 않고 개수만 둔다.
    """

    __tablename__ = "deletion_requests"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # DATA|ACCOUNT
    profile_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    child_id: Mapped[str | None] = mapped_column(String(48), nullable=True)  # 대상 ID(감사 기록)
    scope: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(40), default="USER_REQUEST")  # 코드값만(자유 입력 저장 안 함)
    requested_by: Mapped[str] = mapped_column(String(40), default="")  # 요청자 user id
    status: Mapped[str] = mapped_column(String(12), default="QUEUED")  # QUEUED|RUNNING|SUCCEEDED|FAILED|CANCELLED
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 즉시 숨김 시각
    effective_at: Mapped[datetime] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)  # {"stories": 3, ...} 개수만
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
