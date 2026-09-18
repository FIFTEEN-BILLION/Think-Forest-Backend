"""v1 책장(이야기 수정·단어장·단어 퀴즈·이야기책) 요청/응답 스키마.

키는 camelCase, 시각은 ISO 8601 UTC(`Z`) 문자열. 점수 필드는 어디에도 두지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..schemas.common import CamelModel
from .schemas_conversation import StoryOut, StorySummary, TopicRef

WordStatus = Literal["NEW", "PRACTICING", "FAMILIAR"]
QuizMode = Literal["MEANING_TO_WORD", "WORD_TO_MEANING", "FILL_IN_BLANK"]
BookStatus = Literal["DRAFT", "COMPLETED"]


# --- 이야기 수정 · 상세 ----------------------------------------------------------


class ThoughtJourneyPatch(CamelModel):
    initial_idea: str | None = Field(default=None, max_length=300)
    evidence: list[str] | None = Field(default=None, max_length=10)
    alternatives: list[str] | None = Field(default=None, max_length=10)
    final_reflection: str | None = Field(default=None, max_length=300)


class StoryPatchRequest(CamelModel):
    """아이가 고친 내용. AI 정리본 원본(`aiOriginal`)은 이 요청으로 바뀌지 않는다."""

    title: str | None = Field(default=None, min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=4000)
    thought_journey: ThoughtJourneyPatch | None = None
    version: int | None = Field(default=None, ge=1, description="If-Match 헤더 대신 쓰는 낙관적 잠금 값")

    @model_validator(mode="after")
    def _at_least_one(self) -> StoryPatchRequest:
        if self.title is None and self.summary is None and self.body is None and self.thought_journey is None:
            raise ValueError("empty_patch")
        return self


class WordUsed(CamelModel):
    id: str
    word: str
    meaning: str
    status: WordStatus


class SourceConversation(CamelModel):
    conversation_id: str
    topic: TopicRef
    status: str
    message_count: int
    started_at: str
    completed_at: str | None


class StoryDetail(CamelModel):
    """`GET /stories/{id}` — 이야기 + 이 이야기에서 담은 낱말 + 원본 대화 요약."""

    story: StoryOut
    words_used: list[WordUsed]
    source_conversation: SourceConversation | None


class StoryEdited(CamelModel):
    story: StoryOut
    edited: bool = Field(description="아이가 고친 뒤인지(version > 1)")


# --- 단어장 ----------------------------------------------------------------------


class WordbookSource(CamelModel):
    """명세 13절: 낱말을 만난 자리."""

    conversation_id: str | None
    message_id: str | None


class WordbookEntryOut(CamelModel):
    id: str
    word: str
    reading: str
    meaning: str
    example: str
    my_sentence: str | None
    status: WordStatus
    source: WordbookSource
    # 뜻풀이를 누가 썼는지(AI 인지 검수 사전인지). 명세 밖 추가 필드.
    meaning_source: Literal["ai", "fallback"]
    source_sentence: str
    last_reviewed_at: str | None
    next_review_at: str | None
    created_at: str
    updated_at: str


class WordbookSummary(CamelModel):
    total: int
    familiar: int
    practicing: int
    new_this_week: int
    # 표시용 추가 필드(명세 밖)
    new: int
    due_for_review: int


class WordbookList(CamelModel):
    summary: WordbookSummary
    items: list[WordbookEntryOut]
    next_cursor: str | None


class WordbookEntryResponse(CamelModel):
    entry: WordbookEntryOut


class WordbookCreateRequest(CamelModel):
    word: str = Field(min_length=1, max_length=30)
    conversation_id: str | None = Field(default=None, max_length=48)
    message_id: str = Field(min_length=1, max_length=48, description="낱말이 나온 대화 메시지")


class WordbookPatchRequest(CamelModel):
    status: WordStatus | None = None
    my_sentence: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _at_least_one(self) -> WordbookPatchRequest:
        if self.status is None and self.my_sentence is None:
            raise ValueError("empty_patch")
        return self


# --- 단어 퀴즈 --------------------------------------------------------------------


class QuizOption(CamelModel):
    id: str
    label: str


class QuizQuestionOut(CamelModel):
    """정답 보기 id 는 담지 않는다. 답을 보내면 응답으로 알려 준다."""

    id: str
    index: int
    prompt: str
    options: list[QuizOption]
    answered: bool


class WordQuizOut(CamelModel):
    id: str
    mode: QuizMode
    status: Literal["IN_PROGRESS", "COMPLETED"]
    question_count: int
    answered_count: int
    questions: list[QuizQuestionOut]
    created_at: str
    completed_at: str | None


class QuizCreateRequest(CamelModel):
    count: int | None = Field(default=None, ge=1, le=20)
    mode: QuizMode = "MEANING_TO_WORD"
    status: WordStatus | None = Field(default=None, description="이 상태의 낱말로만 낸다")


class QuizAnswerRequest(CamelModel):
    question_id: str = Field(min_length=1, max_length=48)
    option_id: str = Field(min_length=1, max_length=12)
    # 기기에서 답한 시각. 기록에는 쓰지 않고 받기만 한다(명세 13절 예시 본문).
    client_answered_at: str | None = None


class QuizAnswerResult(CamelModel):
    question_id: str
    correct: bool
    correct_option_id: str


class QuizProgress(CamelModel):
    id: str
    status: Literal["IN_PROGRESS", "COMPLETED"]
    question_count: int
    answered_count: int


class QuizAnswerResponse(CamelModel):
    """점수는 없다. 문항 결과와 낱말 상태·다음 복습 시각만 돌려준다."""

    result: QuizAnswerResult
    entry: WordbookEntryOut
    quiz: QuizProgress


# --- 이야기책 --------------------------------------------------------------------


class BookCover(CamelModel):
    theme: str | None = Field(default=None, max_length=20)
    emoji: str | None = Field(default=None, max_length=8)


class BookSummary(CamelModel):
    id: str
    title: str
    introduction: str
    cover: BookCover | None
    status: BookStatus
    story_count: int
    version: int
    created_at: str
    updated_at: str
    completed_at: str | None


class BookDetail(BookSummary):
    introduction_source: Literal["ai", "fallback"] | None
    stories: list[StorySummary]


class BookList(CamelModel):
    items: list[BookSummary]
    next_cursor: str | None


class BookResponse(CamelModel):
    book: BookDetail


class BookCreateRequest(CamelModel):
    title: str = Field(min_length=1, max_length=60)
    story_ids: list[str] = Field(default_factory=list, max_length=30)
    generate_introduction: bool = False
    cover: BookCover | None = None


class BookPatchRequest(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=60)
    introduction: str | None = Field(default=None, max_length=600)
    cover: BookCover | None = None
    story_ids: list[str] | None = Field(default=None, max_length=30, description="지금 담긴 이야기들의 새 순서")
    version: int | None = Field(default=None, ge=1, description="If-Match 헤더 대신 쓰는 낙관적 잠금 값")

    @model_validator(mode="after")
    def _at_least_one(self) -> BookPatchRequest:
        if self.title is None and self.introduction is None and self.cover is None and self.story_ids is None:
            raise ValueError("empty_patch")
        return self


class BookStoryAddRequest(CamelModel):
    story_id: str = Field(min_length=1, max_length=48)
    position: int | None = Field(default=None, ge=0, le=30)


# --- LLM 구조화 출력(내부) ---------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BookIntroLLM(_Strict):
    introduction: str
