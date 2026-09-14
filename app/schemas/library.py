"""단어 보관함·퀴즈·이야기책·공유·성장 기록·상담·음성 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .common import AiMeta, CamelModel
from .talk import StoryOut, WordNote


class WordIn(CamelModel):
    word: str = Field(min_length=1, max_length=30)
    meaning: str = Field(min_length=1, max_length=160)
    example: str = Field(default="", max_length=200)
    talk_id: str | None = None


class WordOut(CamelModel):
    id: str
    word: str
    meaning: str
    example: str
    quiz_seen: int
    quiz_correct: int
    learned: bool
    created_at: datetime


class ExplainRequest(CamelModel):
    text: str = Field(min_length=1, max_length=1000)


class ExplainResponse(AiMeta):
    words: list[WordNote]


class QuizCreateRequest(CamelModel):
    count: int = Field(default=5, ge=1, le=10)
    word_ids: list[str] | None = Field(default=None, max_length=10)


class QuizQuestionOut(CamelModel):
    index: int
    word_id: str
    prompt: str
    options: list[str]


class AnswerIn(CamelModel):
    index: int = Field(ge=0)
    chosen: int = Field(ge=0)


class AnswerOut(CamelModel):
    index: int
    chosen: int
    correct: bool
    word: str


class QuizOut(CamelModel):
    id: str
    assigned_by: Literal["child", "guardian"]
    questions: list[QuizQuestionOut]
    answers: list[AnswerOut]
    completed: bool
    created_at: datetime


class BookIn(CamelModel):
    title: str = Field(min_length=1, max_length=40)
    story_ids: list[str] = Field(min_length=1, max_length=20)


class BookOut(CamelModel):
    id: str
    title: str
    stories: list[StoryOut]
    created_at: datetime


Visibility = Literal["family", "circle", "community"]


class ShareRequest(CamelModel):
    kind: Literal["story", "book"]
    ref_id: str
    visibility: Visibility
    circle_id: str | None = None


class AdventureIn(CamelModel):
    category: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=40)
    hook: str = Field(min_length=5, max_length=120)
    follow_ups: list[str] = Field(default_factory=list, max_length=3, description="연결·반례·상상 순서")
    visibility: Visibility
    circle_id: str | None = None


class DecisionIn(CamelModel):
    approve: bool


class ShareOut(CamelModel):
    id: str
    kind: str
    title: str
    author_label: str
    visibility: str
    status: str
    body: dict
    created_at: datetime
    published_at: datetime | None


class DayOut(CamelModel):
    date: str
    weekday_label: str
    active: bool


class FrequencyOut(CamelModel):
    talks_started: int
    talks_completed: int
    active_minutes: int
    days_active_7: int
    days_active_30: int
    last7: list[DayOut]


class AchievementOut(CamelModel):
    id: str
    title: str
    description: str
    count: int
    stage: str | None
    next_target: int | None


class WordsProgressOut(CamelModel):
    saved: int
    learned: int


class ProgressOut(CamelModel):
    frequency: FrequencyOut
    achievements: list[AchievementOut]
    words: WordsProgressOut
    stories: int
    books: int


class ConsultationOut(CamelModel):
    id: str
    source: str
    summary: dict
    created_at: datetime


class TranscriptionResponse(CamelModel):
    text: str


class RealtimeSessionResponse(CamelModel):
    client_secret: str = Field(description="브라우저가 OpenAI Realtime 에 직접 붙을 임시 키")
    expires_at: int
    model: str
