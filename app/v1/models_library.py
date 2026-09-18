"""v1 책장 테이블 — 단어장, 단어 퀴즈, 이야기책.

- 모두 새 테이블이다. 기존 테이블(words·word_quizzes·books …) 스키마는 건드리지 않는다.
- 행은 아이 프로필(profile_id) 단위로 갈라 둔다. user_id 는 이야기(story_records)와 이어 보기 위한 열이다.
- 점수를 저장하지 않는다. 퀴즈 결과는 단어 상태(NEW·PRACTICING·FAMILIAR)와 다음 복습 시각만 바꾼다.
- JSON 컬럼은 제자리 수정이 추적되지 않으므로 항상 새 객체로 바꿔 넣는다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .models import _now, prefixed_id

WORD_STATUSES = ("NEW", "PRACTICING", "FAMILIAR")


class WordbookEntry(Base):
    """아이가 대화 중에 담아 둔 낱말 한 개. 뜻풀이는 AI(막히면 검수 사전)가 만든다."""

    __tablename__ = "wordbook_entries"
    __table_args__ = (
        UniqueConstraint("profile_id", "word"),
        Index("ix_wordbook_entries_profile_status", "profile_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("wbe"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    word: Mapped[str] = mapped_column(String(30))
    meaning: Mapped[str] = mapped_column(String(160), default="")
    example: Mapped[str] = mapped_column(String(200), default="")
    my_sentence: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 아이가 직접 쓴 문장
    status: Mapped[str] = mapped_column(String(12), default="NEW")  # NEW|PRACTICING|FAMILIAR
    source: Mapped[str] = mapped_column(String(12), default="fallback")  # ai|fallback — 뜻풀이를 누가 만들었나
    source_conversation_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    source_sentence: Mapped[str] = mapped_column(Text, default="")  # 낱말이 나온 문장(개인정보 가린 원문)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class WordQuizV1(Base):
    """단어 퀴즈 한 판. 맞힌 개수를 남기지 않는다(문항별 결과는 단어 상태로만 간다)."""

    __tablename__ = "word_quizzes_v1"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("wqz"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20))  # MEANING_TO_WORD|WORD_TO_MEANING|FILL_IN_BLANK
    status: Mapped[str] = mapped_column(String(12), default="IN_PROGRESS")  # IN_PROGRESS|COMPLETED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WordQuizQuestion(Base):
    """퀴즈 문항. 정답 보기 id 는 서버에만 두고 응답에 내보내지 않는다(답하기 전까지)."""

    __tablename__ = "word_quiz_questions"
    __table_args__ = (UniqueConstraint("quiz_id", "position"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("wqq"))
    quiz_id: Mapped[str] = mapped_column(ForeignKey("word_quizzes_v1.id"), index=True)
    entry_id: Mapped[str] = mapped_column(ForeignKey("wordbook_entries.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(String(200))
    options: Mapped[list] = mapped_column(JSON, default=list)  # [{"id", "label"}]
    answer_option_id: Mapped[str] = mapped_column(String(12))
    chosen_option_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class StoryBook(Base):
    """이야기 여러 편을 묶은 나만의 책. 책을 지워도 이야기는 남는다."""

    __tablename__ = "story_books"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("bok"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(60))
    introduction: Mapped[str] = mapped_column(Text, default="")
    introduction_source: Mapped[str | None] = mapped_column(String(12), nullable=True)  # ai|fallback|null
    cover: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"theme", "emoji"}
    status: Mapped[str] = mapped_column(String(12), default="DRAFT")  # DRAFT|COMPLETED
    version: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StoryBookItem(Base):
    """책에 담긴 이야기 한 편과 그 순서. 이야기 자체는 story_records 에 그대로 있다."""

    __tablename__ = "story_book_items"
    __table_args__ = (UniqueConstraint("book_id", "story_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("bki"))
    book_id: Mapped[str] = mapped_column(ForeignKey("story_books.id"), index=True)
    story_id: Mapped[str] = mapped_column(ForeignKey("story_records.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
