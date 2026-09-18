"""v1 성장 리포트·보호자 상담 요청/응답 스키마.

리포트는 **능력 진단이 아니다.** 점수·등급·발달 단계를 담지 않고 관찰된 횟수만 담는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..schemas.common import CamelModel

Period = Literal["7d", "30d", "90d"]

NOTICE = "대화에서 관찰된 활동 기록이며 능력이나 발달에 대한 진단이 아닙니다."


class PeriodOut(CamelModel):
    from_: str = Field(alias="from", description="KST 날짜(포함)")
    to: str = Field(description="KST 날짜(포함)")


class ActivityCounts(CamelModel):
    active_days: int
    completed_stories: int
    continued_stories: int
    new_words: int


class ObservedBehaviors(CamelModel):
    """세어 본 횟수만. 잘함/못함을 뜻하지 않는다."""

    full_sentence_responses: int
    reason_explanations: int
    alternative_ideas: int
    revised_ideas: int


class TimelinePoint(CamelModel):
    date: str
    conversations: int
    completed_stories: int
    responses: int


class CategoryCount(CamelModel):
    category: str
    completed_stories: int


class ProgressResponse(CamelModel):
    profile_id: str
    period: PeriodOut
    activity: ActivityCounts
    observed_behaviors: ObservedBehaviors
    timeline: list[TimelinePoint]
    category_breakdown: list[CategoryCount]
    notice: str = NOTICE


# --- 서술형 요약 ----------------------------------------------------------------


class SummaryCreate(CamelModel):
    profile_id: str | None = None
    from_: str | None = Field(default=None, alias="from", description="KST 날짜(YYYY-MM-DD, 포함)")
    to: str | None = Field(default=None, description="KST 날짜(YYYY-MM-DD, 포함)")


class SummaryBody(CamelModel):
    highlights: list[str]
    suggestions: list[str]
    conversation_tips: list[str]
    evidence_story_ids: list[str]


class SummaryOut(CamelModel):
    id: str
    profile_id: str
    period: PeriodOut
    status: Literal["CURRENT", "STALE"]
    source: Literal["ai", "fallback"]
    summary: SummaryBody
    notice: str = NOTICE
    created_at: str
    updated_at: str


class SummaryResponse(CamelModel):
    summary: SummaryOut
    reused: bool = Field(default=False, description="같은 기간·같은 원본 버전의 요약을 그대로 돌려줬는지")


# --- 보호자 월간 상담 ------------------------------------------------------------


class EligibilityResponse(CamelModel):
    profile_id: str
    eligible: bool
    period: str = Field(description="만들 수 있는 달(YYYY-MM, KST)")
    reason: str
    days_remaining: int
    completed_stories: int
    already_created: bool


class ConsultationBody(CamelModel):
    observed_behaviors: list[str]
    examples: list[str]
    questions_to_try: list[str]
    evidence_story_ids: list[str]


class ConsultationOut(CamelModel):
    id: str
    profile_id: str
    period: str
    source: Literal["ai", "fallback"]
    consultation: ConsultationBody
    questions: list[ConsultationQuestionOut] = Field(default_factory=list)
    notice: str = NOTICE
    created_at: str


class ConsultationSummary(CamelModel):
    id: str
    profile_id: str
    period: str
    source: Literal["ai", "fallback"]
    created_at: str


class ConsultationList(CamelModel):
    items: list[ConsultationSummary]
    next_cursor: str | None


class ConsultationCreate(CamelModel):
    profile_id: str | None = None
    period: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM(KST). 비우면 지난달")


class ConsultationQuestionCreate(CamelModel):
    question: str = Field(min_length=2, max_length=300)


class ConsultationQuestionOut(CamelModel):
    id: str
    question: str
    answer: str
    source: Literal["ai", "fallback"]
    created_at: str


class ConsultationQuestionResponse(CamelModel):
    question: ConsultationQuestionOut


class ConsultationResponse(CamelModel):
    consultation: ConsultationOut


# --- LLM 구조화 출력(내부) ------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportSummaryLLM(_Strict):
    highlights: list[str]
    suggestions: list[str]
    conversation_tips: list[str]


class MonthlyConsultationLLM(_Strict):
    observed_behaviors: list[str]
    examples: list[str]
    questions_to_try: list[str]


class ConsultationAnswerLLM(_Strict):
    answer: str


ConsultationOut.model_rebuild()
