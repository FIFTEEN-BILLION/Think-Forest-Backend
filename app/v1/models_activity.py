"""v1 활동·주제 카테고리·요일 편성 테이블.

모두 새 테이블이다. 기존 테이블 스키마는 절대 바꾸지 않는다(운영에 마이그레이션 도구가 없다).
- `activity_sessions`: 생각 모험 활동의 진행 중 초안. 화면 상태가 아니라 서버가 다시 검증할 수 있는 값만 담는다.
- `topic_categories_v1`: 아이가 직접 만든 주제 카테고리. 기본 카테고리는 코드 상수라 행이 없다.
- `topic_schedules`: 운영자가 짜는 요일·기간별 추천 편성. 홈 추천 '순서'에만 영향을 준다.
JSON 컬럼은 제자리 수정이 추적되지 않으므로 항상 새 객체로 바꿔 넣는다.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .models import _now, prefixed_id


class ActivitySession(Base):
    """활동 한 번의 진행 초안. 단계 이동·완료 판정은 서버가 `state` 를 보고 다시 계산한다."""

    __tablename__ = "activity_sessions"
    __table_args__ = (Index("ix_activity_sessions_profile_status", "profile_id", "status"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("act"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    activity_id: Mapped[str] = mapped_column(String(40))
    track: Mapped[str] = mapped_column(String(12))  # forest|lab|theater
    title: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")  # ACTIVE|COMPLETED|CANCELLED
    step: Mapped[int] = mapped_column(Integer, default=0)
    # 마지막 저장 번호. PATCH 의 clientRevision 과 다르면 409(늦게 온 쓰기를 덮어쓰지 않는다).
    revision: Mapped[int] = mapped_column(Integer, default=0)
    min_characters: Mapped[int] = mapped_column(Integer, default=15)
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # activity_rules 의 초안(공백 제외 글자 수로 판정)
    story_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 완료 응답(재요청 시 그대로 돌려준다)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TopicCategoryRow(Base):
    """아이가 만든 주제 카테고리. 만든 프로필에게만 보이고 기본 카테고리와 이름이 겹칠 수 없다."""

    __tablename__ = "topic_categories_v1"
    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_topic_categories_v1_profile_name"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("tcat"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    name: Mapped[str] = mapped_column(String(20))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TopicSchedule(Base):
    """요일·기간별 추천 편성. 아이의 자율 선택은 막지 않고 홈 추천 순서만 바꾼다."""

    __tablename__ = "topic_schedules"
    __table_args__ = (Index("ix_topic_schedules_period", "starts_on", "ends_on"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("tsch"))
    topic_id: Mapped[str] = mapped_column(String(64))  # topic_catalog 의 은행 주제 id
    topic_title: Mapped[str] = mapped_column(String(80), default="")
    category: Mapped[str] = mapped_column(String(20), default="SCIENCE")
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=월 … 6=일(KST). null 이면 매일
    starts_on: Mapped[date] = mapped_column(Date)  # 적용 기간(KST 날짜, 양끝 포함)
    ends_on: Mapped[date] = mapped_column(Date)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(80))  # 홈에 그대로 보여 줄 사람이 읽는 추천 이유
    created_by: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
