"""v1 공유·친구 이야기·신고·리포트·보호자 상담 테이블.

- 모두 새 테이블이다. 기존 테이블(shared_items, share_reports, consultations …)은 건드리지 않는다.
- 공개본(public_stories)은 승인 시점의 **스냅숏**이다. 아이가 원본을 고쳐도 공개본은 저절로 바뀌지 않는다.
- 공개본에는 실제 이름·학교·정확한 나이를 넣지 않는다(별명과 넓은 나이대만).
- JSON 컬럼은 제자리 수정이 추적되지 않으므로 항상 새 객체로 바꿔 넣는다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .models import _now, prefixed_id


class ShareRequest(Base):
    """이야기 공유 요청. DRAFT → PENDING_GUARDIAN → APPROVED → PUBLISHED, 그리고 REJECTED·CANCELLED·REVOKED."""

    __tablename__ = "share_requests_v1"
    __table_args__ = (Index("ix_share_requests_v1_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("shr"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    story_id: Mapped[str] = mapped_column(ForeignKey("story_records.id"), index=True)
    audience: Mapped[str] = mapped_column(String(12))  # PEERS|FAMILY|INVITED
    hide_profile: Mapped[bool] = mapped_column(Boolean, default=True)
    # DRAFT|PENDING_GUARDIAN|APPROVED|PUBLISHED|REJECTED|CANCELLED|HIDDEN|REVOKED
    status: Mapped[str] = mapped_column(String(20), default="PENDING_GUARDIAN")
    # 요청을 올릴 때의 본문 버전. 보호자는 이 버전을 확인하고 승인한다.
    requested_body_version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_body_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_redactions: Mapped[bool] = mapped_column(Boolean, default=False)
    reject_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # 승인 뒤 본문이 바뀌어 공개를 멈춘 경우의 사유(STORY_EDITED).
    pending_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    public_story_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class PublicStory(Base):
    """승인된 공개본 스냅숏. 친구들의 이야기 목록·상세는 이 표만 읽는다."""

    __tablename__ = "public_stories"
    __table_args__ = (Index("ix_public_stories_status_published", "status", "published_at"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("pub"))
    share_request_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    story_id: Mapped[str] = mapped_column(String(48), index=True)
    author_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    audience: Mapped[str] = mapped_column(String(12), default="PEERS")
    # 화면에 보일 별명(hideProfile 이면 '친구'). 실제 이름·학교는 저장하지 않는다.
    display_name: Mapped[str] = mapped_column(String(20), default="친구")
    # 넓은 나이대만("초등 저학년"). 정확한 학년·나이는 저장하지 않는다.
    age_band: Mapped[str] = mapped_column(String(20), default="또래 친구")
    category: Mapped[str] = mapped_column(String(20), default="IMAGINATION")
    title: Mapped[str] = mapped_column(String(80), default="")
    excerpt: Mapped[str] = mapped_column(String(160), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    thought_journey: Mapped[dict] = mapped_column(JSON, default=dict)
    body_version: Mapped[int] = mapped_column(Integer, default=1)
    # PUBLISHED|PAUSED|HIDDEN|REVOKED|DELETED
    status: Mapped[str] = mapped_column(String(12), default="PUBLISHED")
    recommendation_count: Mapped[int] = mapped_column(Integer, default=0)
    report_count: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class StoryRecommendation(Base):
    """따뜻한 추천. 한 사용자는 한 공개본에 한 번만 남길 수 있다."""

    __tablename__ = "story_recommendations"
    __table_args__ = (UniqueConstraint("public_story_id", "user_id", name="uq_story_recommendation_once"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("rec"))
    public_story_id: Mapped[str] = mapped_column(String(48), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CommunityReport(Base):
    """친구 이야기 신고. 같은 사람이 같은 이야기를 여러 번 신고해도 한 건으로 센다."""

    __tablename__ = "community_reports_v1"
    __table_args__ = (UniqueConstraint("public_story_id", "reporter_user_id", name="uq_community_report_once"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("crp"))
    public_story_id: Mapped[str] = mapped_column(String(48), index=True)
    reporter_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(String(30))
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="OPEN")  # OPEN|RESOLVED
    resolution: Mapped[str | None] = mapped_column(String(10), nullable=True)  # KEEP|HIDE|DELETE
    resolution_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(48), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ReportSummary(Base):
    """기간별 서술형 요약. 같은 기간·같은 원본 버전이면 다시 만들지 않고 이 행을 돌려준다."""

    __tablename__ = "report_summaries"
    __table_args__ = (Index("ix_report_summaries_profile_period", "profile_id", "period_from", "period_to"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("rsm"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(48), index=True)
    period_from: Mapped[str] = mapped_column(String(10))  # KST 날짜(YYYY-MM-DD, 포함)
    period_to: Mapped[str] = mapped_column(String(10))
    # 원본 기록(이야기 id·버전·수정 시각)의 지문. 달라지면 이전 요약은 STALE 이다.
    source_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10), default="CURRENT")  # CURRENT|STALE
    source: Mapped[str] = mapped_column(String(10), default="fallback")  # ai|fallback
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class GuardianConsultation(Base):
    """보호자 월간 상담(관찰 기록). 근거가 된 이야기 id 를 함께 남긴다."""

    __tablename__ = "consultations_v1"
    __table_args__ = (UniqueConstraint("profile_id", "period", name="uq_consultation_period"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("cns"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(String(48), index=True)
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM(KST)
    source: Mapped[str] = mapped_column(String(10), default="fallback")  # ai|fallback
    # {"observedBehaviors": [...], "examples": [...], "questionsToTry": [...], "evidenceStoryIds": [...]}
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ConsultationQuestion(Base):
    """상담을 읽은 보호자의 후속 질문과 답."""

    __tablename__ = "consultation_questions_v1"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("cnq"))
    consultation_id: Mapped[str] = mapped_column(String(48), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    question: Mapped[str] = mapped_column(String(300))
    answer: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="fallback")  # ai|fallback
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
