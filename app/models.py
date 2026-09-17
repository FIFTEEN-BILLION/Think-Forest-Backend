"""영속 모델.

- 보호자(가족)가 아이를 만들고 권한을 준다. 아이는 태블릿 토큰으로 혼자 쓴다.
- 아이 문장은 대화·이야기에만 두고 저장 전에 개인정보를 가린다.
- 학교 이름·주소·전화번호·생년월일은 저장하지 않는다(학년과 소속 종류만).
- JSON 컬럼은 제자리 수정이 추적되지 않으므로 항상 새 객체로 바꿔 넣는다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from . import clock
from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return clock.now()


class Family(Base):
    __tablename__ = "families"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    guardian_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Child(Base):
    __tablename__ = "children"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    nickname: Mapped[str] = mapped_column(String(20), default="")
    grade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    affiliation: Mapped[str | None] = mapped_column(String(20), nullable=True)  # elementary|homeschool|other
    likes: Mapped[list] = mapped_column(JSON, default=list)
    want_to_learn: Mapped[list] = mapped_column(JSON, default=list)
    profile_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 성인 테스터 계정. demo 모드에서도 실제 AI 를 쓸 수 있다(아동 데이터가 아니므로).
    is_tester: Mapped[bool] = mapped_column(Boolean, default=False)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    onboarding: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DeviceToken(Base):
    __tablename__ = "device_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Category(Base):
    """아이가 '!' 버튼으로 직접 만든 카테고리."""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("child_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    name: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Talk(Base):
    __tablename__ = "talks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    mode: Mapped[str] = mapped_column(String(10))  # topic|diary
    category: Mapped[str] = mapped_column(String(20))
    custom_category_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shared_item_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    topic: Mapped[dict] = mapped_column(JSON, default=dict)  # 주제 스냅숏(검수 사실 포함)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active|completed
    active_seconds: Mapped[int] = mapped_column(Integer, default=0)
    ai_calls: Mapped[int] = mapped_column(Integer, default=0)
    story_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_turn_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    talk_id: Mapped[str] = mapped_column(ForeignKey("talks.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(8))  # friend|child
    move: Mapped[str] = mapped_column(String(16))  # friend: 던진 질문 종류 / child: 답한 질문 종류
    text: Mapped[str] = mapped_column(Text)
    sentence_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    input_mode: Mapped[str] = mapped_column(String(8), default="text")  # text|voice
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Story(Base):
    """대화·일기로 만든 이야기 플롯 완성본."""

    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    talk_id: Mapped[str] = mapped_column(ForeignKey("talks.id"), index=True)
    title: Mapped[str] = mapped_column(String(80))
    scenes: Mapped[list] = mapped_column(JSON, default=list)
    ending_question: Mapped[str] = mapped_column(String(160), default="")
    source: Mapped[str] = mapped_column(String(10))  # ai|fallback
    based_on_seq: Mapped[int] = mapped_column(Integer, default=0)  # 이 순번까지의 대화로 만들었다
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Book(Base):
    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    title: Mapped[str] = mapped_column(String(80))
    story_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Word(Base):
    """단어 보관함."""

    __tablename__ = "words"
    __table_args__ = (UniqueConstraint("child_id", "word"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    word: Mapped[str] = mapped_column(String(30))
    meaning: Mapped[str] = mapped_column(String(160))
    example: Mapped[str] = mapped_column(String(200), default="")
    talk_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quiz_seen: Mapped[int] = mapped_column(Integer, default=0)
    quiz_correct: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WordQuiz(Base):
    __tablename__ = "word_quizzes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    assigned_by: Mapped[str] = mapped_column(String(10))  # child|guardian
    questions: Mapped[list] = mapped_column(JSON, default=list)  # 정답 번호는 서버에만
    answers: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Circle(Base):
    """친구·또래 가족끼리 이야기를 나누는 모임. 초대 코드로 들어온다."""

    __tablename__ = "circles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(30))
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("families.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class CircleMember(Base):
    __tablename__ = "circle_members"

    circle_id: Mapped[str] = mapped_column(ForeignKey("circles.id"), primary_key=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), primary_key=True)


class SharedItem(Base):
    """공유된 모험 이야기. 원본이 아니라 개인정보를 가린 스냅숏을 둔다."""

    __tablename__ = "shared_items"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"), index=True)
    child_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    author_label: Mapped[str] = mapped_column(String(20))  # 별명 또는 "보호자"
    kind: Mapped[str] = mapped_column(String(10))  # story|book|adventure
    ref_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(80))
    body: Mapped[dict] = mapped_column(JSON, default=dict)
    visibility: Mapped[str] = mapped_column(String(10))  # family|circle|community
    circle_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # pending_guardian|pending_review|published|rejected|hidden
    status: Mapped[str] = mapped_column(String(20), index=True)
    moderation: Mapped[dict] = mapped_column(JSON, default=dict)
    report_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ShareReport(Base):
    __tablename__ = "share_reports"
    __table_args__ = (UniqueConstraint("item_id", "family_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("shared_items.id"), index=True)
    family_id: Mapped[str] = mapped_column(ForeignKey("families.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class SafetyEvent(Base):
    """보호자에게 알릴 안전 사건. 아이 원문은 남기지 않고 종류만 둔다."""

    __tablename__ = "safety_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    talk_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str] = mapped_column(String(20))
    escalate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Consultation(Base):
    __tablename__ = "consultations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
