"""v1 활동·주제 카테고리·요일 편성 요청/응답 스키마. 키는 camelCase, 시각은 ISO 8601 UTC(`Z`)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from ..schemas.common import CamelModel

Track = Literal["forest", "lab", "theater"]
ActivityStatus = Literal["ACTIVE", "COMPLETED", "CANCELLED"]
# activity_rules.EVENT_TYPES 와 같은 목록이다(테스트가 두 곳이 갈라지지 않는지 확인한다).
EventType = Literal[
    "TEXT",
    "HINT",
    "TOPIC",
    "KEYWORD",
    "LAB_VALUE",
    "OBSERVATION",
    "APPROVE",
    "SCENE",
    "CHOICE",
    "EMOTION",
    "INQUIRY",
    "RUN",
]


# --- 활동 목록·상세 --------------------------------------------------------------


class ActivityItem(CamelModel):
    id: str
    track: Track
    area: str = Field(description="영역 표시(사고력 · 과학 · 수학 · 역사 · 인성)")
    place: str = Field(description="활동이 있는 곳의 이름")
    title: str
    subtitle: str
    level: str
    tags: list[str]
    description: str
    estimated_minutes: int
    min_characters: int = Field(description="글쓰기 단계에서 공백을 뺀 최소 글자 수")


class ActivityList(CamelModel):
    items: list[ActivityItem]
    next_cursor: str | None


class ActivityVisuals(CamelModel):
    kind: str = Field(description="EVIDENCE · CONTROL · CONDITIONS · EMOTIONS · MAP")
    icon: str
    color: str
    items: list[str]


class ActivityDetail(ActivityItem):
    intro: str
    clue: str | None
    steps: list[str]
    questions: list[str]
    visuals: ActivityVisuals


class ActivityDetailResponse(CamelModel):
    activity: ActivityDetail


# --- 활동 세션 ------------------------------------------------------------------


class ActivityAnswer(CamelModel):
    question: str
    text: str


class ActivityStep(CamelModel):
    index: int
    label: str
    total: int
    writing: bool = Field(description="이 단계가 글쓰기 단계인가")


class MissingCondition(CamelModel):
    code: str
    message: str


class ActivityDraft(CamelModel):
    text: str = ""
    answers: list[ActivityAnswer] = Field(default_factory=list)
    followup: str = ""
    hints: int = 0
    lab: dict[str, Any] = Field(default_factory=dict)
    theater: dict[str, Any] = Field(default_factory=dict)
    inquiry: dict[str, Any] | None = None
    path: dict[str, Any] | None = None


class ActivitySessionOut(CamelModel):
    session_id: str
    activity_id: str
    track: Track
    title: str
    status: ActivityStatus
    revision: int = Field(description="자동 저장 번호. PATCH 의 clientRevision 에 그대로 넣는다")
    step: ActivityStep
    min_characters: int
    draft: ActivityDraft
    missing: list[MissingCondition] = Field(description="지금 단계에서 아직 못 채운 조건")
    ready_to_complete: bool
    story_id: str | None
    started_at: str
    updated_at: str


class ActivitySessionResponse(CamelModel):
    session: ActivitySessionOut


class ActivityStartRequest(CamelModel):
    activity_id: str = Field(min_length=1, max_length=40)
    keyword: str = Field(default="", max_length=40, description="마음극장 활동의 마음 키워드")


class ActivityEvent(CamelModel):
    type: EventType
    field: str | None = Field(default=None, max_length=30)
    value: Any = None


class ActivityPatchRequest(CamelModel):
    client_revision: int = Field(ge=0)
    event: ActivityEvent


class ActivityCompleteResponse(CamelModel):
    session: ActivitySessionOut
    story: ActivityStoryOut


class ActivityStoryOut(CamelModel):
    id: str
    title: str
    summary: str
    body: str
    category: str
    answers: list[ActivityAnswer]
    created_at: str


# --- 주제 카테고리 ---------------------------------------------------------------


class TopicCategoryOut(CamelModel):
    id: str
    name: str
    kind: Literal["DEFAULT", "USER"]
    order: int
    visual: str
    editable: bool = Field(description="기본 카테고리는 false — 수정·삭제할 수 없다")


class TopicCategoryList(CamelModel):
    items: list[TopicCategoryOut]
    next_cursor: str | None = Field(default=None, description="기본 5 + 사용자 20개가 최대라 항상 한 번에 준다(null)")


class TopicCategoryResponse(CamelModel):
    category: TopicCategoryOut


class TopicCategoryCreateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=20)
    order: int | None = Field(default=None, ge=0, le=999)


class TopicCategoryUpdateRequest(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=20)
    order: int | None = Field(default=None, ge=0, le=999)

    @model_validator(mode="after")
    def _at_least_one(self) -> TopicCategoryUpdateRequest:
        if self.name is None and self.order is None:
            raise ValueError("name_or_order_required")
        return self


# --- 요일별 주제 운영 -------------------------------------------------------------


class TopicScheduleOut(CamelModel):
    id: str
    topic_id: str
    topic_title: str
    category: str
    weekday: int | None = Field(description="0=월 … 6=일(KST). null 이면 기간 내 매일")
    starts_on: str
    ends_on: str
    order: int
    reason: str
    active: bool = Field(description="오늘(KST) 기준으로 지금 적용되는 편성인가")
    created_at: str
    updated_at: str


class TopicScheduleList(CamelModel):
    items: list[TopicScheduleOut]
    next_cursor: str | None


class TopicScheduleResponse(CamelModel):
    schedule: TopicScheduleOut


class TopicScheduleCreateRequest(CamelModel):
    topic_id: str = Field(min_length=1, max_length=64)
    starts_on: date
    ends_on: date
    weekday: int | None = Field(default=None, ge=0, le=6)
    order: int = Field(default=0, ge=0, le=999)
    reason: str = Field(min_length=1, max_length=80, description="홈에 그대로 보여 줄 사람이 읽는 문장")

    @model_validator(mode="after")
    def _period(self) -> TopicScheduleCreateRequest:
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on_before_starts_on")
        return self


class TopicScheduleUpdateRequest(CamelModel):
    starts_on: date | None = None
    ends_on: date | None = None
    weekday: int | None = Field(default=None, ge=0, le=6)
    order: int | None = Field(default=None, ge=0, le=999)
    reason: str | None = Field(default=None, min_length=1, max_length=80)
    clear_weekday: bool = Field(default=False, description="true 면 weekday 를 비워 기간 내 매일로 바꾼다")


ActivityCompleteResponse.model_rebuild()
