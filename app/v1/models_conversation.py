"""v1 대화 테이블(첫인사·이야기 세션, 메시지, 추출 정보, 정리본, 멱등 기록).

- 모두 새 테이블이다. 기존 테이블 스키마는 바꾸지 않는다.
- 메시지 content 는 개인정보를 가린 원문이다. 안전하지 않은 입력은 저장하지 않는다.
- 이야기 정리본(story_records)은 원본 메시지와 따로 둔다. AI 가 만든 원본은 ai_original 에만 둔다.
- JSON 컬럼은 제자리 수정이 추적되지 않으므로 항상 새 객체로 바꿔 넣는다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .models import _now, prefixed_id


class ChildProfile(Base):
    """첫인사로 확정한 아이 프로필. 아이(children) 한 명에 하나."""

    __tablename__ = "child_profiles"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("prf"))
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    nickname: Mapped[str] = mapped_column(String(20), default="")
    school_or_group: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 종류만(초등학교·홈스쿨 등)
    grade_or_age_band: Mapped[str | None] = mapped_column(String(30), nullable=True)
    interests: Mapped[list] = mapped_column(JSON, default=list)
    interest_details: Mapped[list] = mapped_column(JSON, default=list)
    growth_goal: Mapped[str | None] = mapped_column(String(60), nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ConversationSession(Base):
    """첫인사(FIRST_GREETING, fgs_)와 이야기(STORY, cnv_) 세션."""

    __tablename__ = "conversation_sessions"
    __table_args__ = (Index("ix_conversation_sessions_user_kind_status", "user_id", "kind", "status"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # FIRST_GREETING|STORY
    topic: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 이야기 주제 스냅숏
    # ACTIVE|READY_TO_FINISH|FINALIZING|COMPLETED|CANCELLED
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # 첫인사: {"draft": {...}}
    # 이야기: {"covered": [...], "validResponses": n, "extraTurns": n, "readyAnnounced": bool}
    readiness: Mapped[dict] = mapped_column(JSON, default=dict)
    # {"type", "questionId", "options", "move", "prompt", ...} — 완료·취소 뒤에는 null
    current_interaction: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    ai_calls: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    story_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 완료 응답(재요청 시 그대로 돌려준다)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq"),
        UniqueConstraint("session_id", "client_message_id"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("msg"))
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(12))  # USER|ASSISTANT
    content: Mapped[str] = mapped_column(Text)  # 개인정보를 가린 원문
    # USER: {"type", "optionId", "options"(스냅숏)} / ASSISTANT: {"type", "questionId", "options"}(질문 스냅숏)
    answer: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    question_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    client_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(12), default="child")  # child|ai|fallback
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # 판정 신호(차원·문장 여부 등). 원문은 두지 않는다
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ConversationFact(Base):
    """첫인사에서 뽑은 항목. 어느 메시지에서 어떤 신뢰도로 뽑았는지 남겨 잘못 뽑은 값을 추적한다."""

    __tablename__ = "conversation_facts"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("fct"))
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"), index=True)
    field: Mapped[str] = mapped_column(String(30))
    value: Mapped[dict | list | str | None] = mapped_column(JSON, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StoryRecord(Base):
    """이야기 대화의 최종 정리본(나의 책장). 원본 대화와 분리 저장한다."""

    __tablename__ = "story_records"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("sty"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"), unique=True)
    category: Mapped[str] = mapped_column(String(20))
    topic_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic_title: Mapped[str] = mapped_column(String(80), default="")
    title: Mapped[str] = mapped_column(String(80))
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    thought_journey: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_original: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # AI 최초 정리본(없으면 규칙 정리)
    source: Mapped[str] = mapped_column(String(12), default="fallback")  # ai|fallback — 제목을 누가 만들었나
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserTopic(Base):
    """아이가 직접 만든 주제. 만든 사용자에게만 보인다."""

    __tablename__ = "user_topics"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("topic_user"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(60))
    category: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class IdempotencyRecord(Base):
    """`Idempotency-Key`·`clientMessageId` 재요청에 같은 응답을 돌려주기 위한 기록."""

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "key", "route"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("idm"))
    user_id: Mapped[str] = mapped_column(String(48), index=True)
    key: Mapped[str] = mapped_column(String(128))
    route: Mapped[str] = mapped_column(String(120))
    status_code: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ConversationOwner(Base):
    """대화가 속한 아이. 기존 대화 테이블 변경 없이 여러 아이의 기록을 분리한다."""
    __tablename__ = "conversation_owners"
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"), primary_key=True)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
