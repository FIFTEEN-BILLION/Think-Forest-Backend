"""v1 대화·주제·홈·책장 요청/응답 스키마. 키는 camelCase, 시각은 ISO 8601 UTC(`Z`) 문자열."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..schemas.common import CamelModel

InteractionType = Literal["TEXT", "SINGLE_CHOICE"]
SessionStatus = Literal["ACTIVE", "READY_TO_FINISH", "FINALIZING", "COMPLETED", "CANCELLED"]
TopicCategory = Literal["SCIENCE", "MATH", "HISTORY", "THINKING", "DAILY_LIFE", "NATURE", "FEELINGS", "IMAGINATION"]


# --- 메시지 공통 ---------------------------------------------------------------


class GreetingReadinessResponse(CamelModel):
    """현재 동의·서버 설정 검사. 실제 AI 공급자 상태나 사용량 잔여를 보장하지 않는다."""

    available: bool = Field(description="현재 동의·AI 설정상 첫인사에 실제 AI를 사용할 수 있는지")
    reason: Literal["guest_consent_required", "child_data_mode_off", "no_api_key", "ai_disabled"] | None = Field(
        description="차단 사유. 사용 가능하면 null"
    )
    message: str | None = Field(description="사용자에게 표시할 안내. 사용 가능하면 null")


class ChoiceOption(CamelModel):
    id: str
    label: str


class Interaction(CamelModel):
    type: InteractionType
    question_id: str
    options: list[ChoiceOption] = Field(default_factory=list)


class AnswerOut(CamelModel):
    type: InteractionType
    option_id: str | None = None


class MessageOut(CamelModel):
    id: str
    role: Literal["USER", "ASSISTANT"]
    content: str
    question_id: str | None = Field(default=None, description="USER: 답한 질문 / ASSISTANT: 이 메시지가 던진 질문")
    answer: AnswerOut | None = Field(default=None, description="USER 메시지의 답 형식")
    interaction: Interaction | None = Field(
        default=None, description="ASSISTANT 메시지가 던진 질문 형식(선택지 스냅숏)"
    )
    source: Literal["ai", "fallback"] | None = Field(
        default=None, description="ASSISTANT: 실제 AI 문장이면 ai, 규칙 대사면 fallback / USER: null"
    )
    created_at: str


class MessageInput(CamelModel):
    type: InteractionType
    text: str | None = Field(default=None, max_length=1000)
    option_id: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _check(self) -> MessageInput:
        if self.type == "TEXT" and not (self.text or "").strip():
            raise ValueError("text_required")
        if self.type == "SINGLE_CHOICE" and not self.option_id:
            raise ValueError("option_id_required")
        return self


class MessageRequest(CamelModel):
    client_message_id: str = Field(min_length=1, max_length=100)
    question_id: str | None = Field(default=None, max_length=64)
    input: MessageInput


class CompleteRequest(CamelModel):
    trigger: Literal["BUTTON", "CHAT_END_INTENT"] = "BUTTON"


# --- 티키와 첫인사 -------------------------------------------------------------


class ProfileDraftOut(CamelModel):
    nickname: str | None = None
    school_or_group: str | None = None
    grade_or_age_band: str | None = None
    interests: list[str] = Field(default_factory=list)
    interest_details: list[str] = Field(default_factory=list)
    growth_goal: str | None = None


class GreetingReadiness(CamelModel):
    ready: bool
    progress: int
    missing: list[str]


class GreetingProcessing(CamelModel):
    mode: Literal["AI", "RULES"]
    reason: str | None = None


class GreetingSessionOut(CamelModel):
    session_id: str
    status: SessionStatus
    resumed: bool = Field(default=False, description="이미 진행 중인 세션을 돌려줬으면 true")
    messages: list[MessageOut]
    next_cursor: str | None = None
    current_interaction: Interaction | None
    profile_draft: ProfileDraftOut
    readiness: GreetingReadiness
    processing: GreetingProcessing | None = None
    profile_revision: int = 0
    deferred_fields: list[str] = Field(default_factory=list)


class ProfileOut(CamelModel):
    id: str
    nickname: str
    school_or_group: str | None
    grade_or_age_band: str | None
    interests: list[str]
    interest_details: list[str]
    growth_goal: str | None


class GreetingCompletion(CamelModel):
    status: SessionStatus
    profile: ProfileOut
    summary: str
    completed_at: str


class GreetingMessageResponse(CamelModel):
    user_message: MessageOut
    assistant_message: MessageOut
    next_interaction: Interaction | None
    profile_draft: ProfileDraftOut
    readiness: GreetingReadiness
    status: SessionStatus
    end_intent_detected: bool
    completion: GreetingCompletion | None = None
    processing: GreetingProcessing | None = None
    profile_revision: int = 0
    deferred_fields: list[str] = Field(default_factory=list)


class GreetingCompleteRequest(CamelModel):
    trigger: Literal["BUTTON"] = "BUTTON"
    profile_revision: int = Field(ge=0)


# --- 티키와 이야기 -------------------------------------------------------------


class TopicRef(CamelModel):
    id: str | None
    title: str
    category: str


class StoryReadiness(CamelModel):
    ready: bool
    progress: int
    covered_dimensions: list[str]
    missing_dimensions: list[str]


class ConversationStartRequest(CamelModel):
    topic_id: str = Field(min_length=1, max_length=64)
    input_mode: Literal["TEXT", "VOICE"] = "TEXT"
    locale: str = Field(default="ko-KR", max_length=10)


class ConversationStartResponse(CamelModel):
    conversation_id: str
    status: SessionStatus
    topic: TopicRef
    assistant_message: MessageOut
    next_interaction: Interaction | None
    readiness: StoryReadiness


class ThoughtJourney(CamelModel):
    initial_idea: str
    evidence: list[str]
    alternatives: list[str]
    final_reflection: str


class StoryOut(CamelModel):
    id: str
    title: str
    summary: str
    body: str
    thought_journey: ThoughtJourney
    category: str
    topic: TopicRef
    favorite: bool
    version: int
    source_conversation_id: str
    created_at: str
    updated_at: str


class ConversationCompletion(CamelModel):
    status: SessionStatus
    story: StoryOut


class ConversationMessageResponse(CamelModel):
    user_message: MessageOut
    assistant_message: MessageOut
    next_interaction: Interaction | None
    readiness: StoryReadiness
    status: SessionStatus
    end_intent_detected: bool
    completion: ConversationCompletion | None = None


class ConversationDetail(CamelModel):
    conversation_id: str
    status: SessionStatus
    topic: TopicRef
    messages: list[MessageOut]
    next_cursor: str | None
    current_interaction: Interaction | None
    readiness: StoryReadiness
    story_id: str | None
    created_at: str
    updated_at: str


class ConversationSummary(CamelModel):
    conversation_id: str
    status: SessionStatus
    topic: TopicRef
    readiness: StoryReadiness
    story_id: str | None
    created_at: str
    updated_at: str


class ConversationList(CamelModel):
    items: list[ConversationSummary]
    next_cursor: str | None


class CancelResponse(CamelModel):
    conversation_id: str
    status: SessionStatus
    cancelled_at: str | None


# --- 주제 ----------------------------------------------------------------------


class TopicItem(CamelModel):
    id: str
    title: str
    category: str
    source: Literal["BANK", "USER"]
    hook: str = Field(description="대화를 여는 대표 질문")
    estimated_minutes: int


class TopicList(CamelModel):
    items: list[TopicItem]
    next_cursor: str | None


class TopicDetail(TopicItem):
    questions: list[str] = Field(description="대화에서 이어 갈 대표 질문들")


class TopicDetailResponse(CamelModel):
    topic: TopicDetail


class TopicCreateRequest(CamelModel):
    title: str = Field(min_length=1, max_length=40)
    category: TopicCategory = "IMAGINATION"


class TopicSafety(CamelModel):
    allowed: bool
    reason: str | None


class CreatedTopic(CamelModel):
    id: str
    title: str
    category: str
    source: Literal["USER"]


class TopicCreateResponse(CamelModel):
    topic: CreatedTopic
    safety: TopicSafety


# --- 홈 · /me -----------------------------------------------------------------


class HomeProfile(CamelModel):
    nickname: str | None
    needs_first_greeting: bool


class Recommendation(CamelModel):
    topic_id: str
    title: str
    category: str
    reason: str
    estimated_minutes: int


class ResumeOut(CamelModel):
    conversation_id: str
    title: str
    status: SessionStatus
    updated_at: str


class RecentWord(CamelModel):
    word: str
    meaning: str


class CommunityStoryPreview(CamelModel):
    id: str
    title: str


class WeeklyActivity(CamelModel):
    conversation_days: int
    completed_stories: int


class HomeResponse(CamelModel):
    profile: HomeProfile
    recommendations: list[Recommendation]
    resume: ResumeOut | None
    recent_words: list[RecentWord]
    community_stories: list[CommunityStoryPreview]
    weekly_activity: WeeklyActivity


class MeUser(CamelModel):
    id: str
    role: str
    needs_first_greeting: bool


class MeProfile(CamelModel):
    id: str
    nickname: str
    grade_or_age_band: str | None
    interests: list[str]
    growth_goal: str | None


class MeResponse(CamelModel):
    user: MeUser
    profile: MeProfile | None


# --- 책장 ----------------------------------------------------------------------


class StorySummary(CamelModel):
    id: str
    title: str
    summary: str
    category: str
    favorite: bool
    version: int
    source_conversation_id: str
    created_at: str
    updated_at: str


class StoryList(CamelModel):
    items: list[StorySummary]
    next_cursor: str | None


class StoryDetailResponse(CamelModel):
    story: StoryOut


class FavoriteState(CamelModel):
    id: str
    favorite: bool
    version: int
    updated_at: str


class FavoriteResponse(CamelModel):
    story: FavoriteState


# --- LLM 구조화 출력(내부) ------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


GreetingField = Literal["NICKNAME", "GRADE_OR_AGE", "INTEREST", "INTEREST_DETAIL", "GROWTH_GOAL", "NONE"]
EndIntent = Literal["none", "clear", "unsure"]


GreetingProfileField = Literal[
    "nickname", "schoolOrGroup", "gradeOrAgeBand", "interests", "interestDetails", "growthGoal"
]


class GreetingEvidence(_Strict):
    message_id: str
    quote: str = Field(min_length=1, max_length=1000)


class GreetingChange(_Strict):
    field: GreetingProfileField
    operation: Literal["SET", "ADD", "REMOVE", "CLEAR", "DEFER", "RESUME"]
    value: str | None
    values: list[str]
    evidence: list[GreetingEvidence]


class FirstGreetingLLM(_Strict):
    message: str = Field(min_length=1, max_length=600)
    changes: list[GreetingChange] = Field(max_length=20)
    context_summary: str = Field(max_length=1600)
    propose_review: bool
    profile_summary: str | None = Field(max_length=600)
    end_intent: EndIntent


class ChoiceLLM(_Strict):
    id: str
    label: str


class StoryTurnLLM(_Strict):
    reaction: str
    question: str
    options: list[ChoiceLLM]
    child_idea: str
    shared_experience: bool
    reason_given: bool
    new_idea: bool
    stance: Literal["kept", "changed", "none"]
    end_intent: EndIntent
