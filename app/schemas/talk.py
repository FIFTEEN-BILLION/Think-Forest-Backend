"""생각 친구 대화·이야기·카테고리 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import AiMeta, CamelModel

VisualKey = Literal[
    "thinking", "question", "lightbulb", "magnifier", "puzzle", "castle", "book", "star", "calendar",
    "snow", "sled", "ice", "water_drop", "airplane", "cloud", "car", "pizza", "stairs", "fire", "hangul",
    "school_bag", "sun", "moon", "tree", "animal", "rocket", "heart", "rainbow", "music", "family",
]  # fmt: skip
Move = Literal["hook", "tail", "connect", "challenge", "imagine", "reason_check", "compose", "continue"]


class ThemeOut(CamelModel):
    weekday: int
    weekday_label: str
    category: str
    title: str
    color: str
    mood: str
    visual: str


class TopicOut(CamelModel):
    id: str | None
    category: str
    title: str
    hook: str
    visual: str
    source: str


class CategoryOut(CamelModel):
    id: str
    name: str
    kind: Literal["system", "custom"]
    visual: str


class TodayResponse(CamelModel):
    theme: ThemeOut
    suggestions: list[TopicOut]
    categories: list[CategoryOut]


class CustomTopicIn(CamelModel):
    title: str = Field(min_length=1, max_length=20)
    hook: str = Field(min_length=5, max_length=100)


class TalkStartRequest(CamelModel):
    mode: Literal["topic", "diary"] | None = None
    category: str | None = None
    topic_id: str | None = None
    custom_category_id: str | None = None
    custom_topic: CustomTopicIn | None = None
    shared_item_id: str | None = None


class WordNote(CamelModel):
    word: str
    meaning: str
    example: str = ""


class TurnOut(CamelModel):
    id: str
    seq: int
    role: Literal["friend", "child"]
    move: str
    text: str
    question: str | None = Field(default=None, description="친구 턴: 지금 답해야 할 질문(화면에 계속 보여 준다)")
    sentence_ok: bool | None
    input_mode: str
    visual: str | None
    words: list[WordNote]
    source: str | None
    created_at: datetime


class TimeOut(CamelModel):
    active_seconds: int
    min_seconds: int
    remaining_seconds: int
    can_finish: bool


class StorySceneOut(CamelModel):
    heading: str
    text: str
    from_turn_ids: list[str]
    visual: str


class StoryOut(CamelModel):
    id: str
    talk_id: str
    title: str
    scenes: list[StorySceneOut]
    ending_question: str
    source: str
    created_at: datetime


class TalkOut(CamelModel):
    id: str
    mode: str
    category: str
    topic: TopicOut
    status: str
    pending_move: str
    time: TimeOut
    turns: list[TurnOut]
    story: StoryOut | None


class TalkSummary(CamelModel):
    id: str
    mode: str
    category: str
    title: str
    status: str
    active_seconds: int
    story_id: str | None
    started_at: datetime


class TurnRequest(CamelModel):
    text: str = Field(min_length=1, max_length=2000, description="길이 제한 없이 자유롭게. 문장인지만 확인한다")
    input_mode: Literal["text", "voice"] = "text"


class TurnResponse(AiMeta):
    accepted: bool = Field(description="문장으로 인정되어 대화가 앞으로 나아갔는지")
    safety: str | None = None
    move: str
    child_turn: TurnOut
    friend_turn: TurnOut
    sentence_starters: list[str]
    time: TimeOut
    story: StoryOut | None = None


class CategoryIn(CamelModel):
    name: str = Field(min_length=1, max_length=12)


class TopicSuggestions(AiMeta):
    topics: list[TopicOut]


# --- LLM 구조화 출력(내부) ---------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmWord(_Strict):
    word: str
    meaning: str
    example: str


class TalkTurnLLM(_Strict):
    reaction: str
    question: str
    visual: VisualKey
    hard_words: list[LlmWord]
    child_idea: str
    reason_given: bool
    new_idea: bool
    stance: Literal["kept", "changed", "none"]


class PlotSceneLLM(_Strict):
    heading: str
    text: str
    from_turn_ids: list[str]
    visual: VisualKey


class PlotLLM(_Strict):
    title: str
    scenes: list[PlotSceneLLM]
    ending_question: str


class TopicIdeaLLM(_Strict):
    title: str
    hook: str
    visual: VisualKey


class TopicSuggestLLM(_Strict):
    topics: list[TopicIdeaLLM]


class WordExplainLLM(_Strict):
    words: list[LlmWord]


class ConsultationLLM(_Strict):
    highlights: list[str]
    suggestions: list[str]
    conversation_tips: list[str]
