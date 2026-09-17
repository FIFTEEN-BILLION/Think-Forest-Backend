"""티키 말로 가르치기 스키마.

프로그램 = Step[] (최대 8). 세 단계로 중첩한다(재귀 없음): Step ⊃ Inner ⊃ Leaf.
  Leaf  : move / turn / stop
  Inner : Leaf + if(sensor·state·then·else)
  Step  : Inner + repeat(body = Inner[], "우체국에 갈 때까지 반복")
API 모델은 CamelModel, LLM 구조화 출력 모델은 모든 필드가 필수인 _Strict 모델이다.
개수 제한은 API 모델에만 두고, LLM 출력은 라우터가 정리하면서 자른다(strict 스키마 호환).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .common import AiMeta, CamelModel
from .inquiry import InputOrigin

LeafOp = Literal["move", "turn", "stop"]
InnerOp = Literal["move", "turn", "stop", "if"]
StepOp = Literal["move", "turn", "stop", "if", "repeat"]
Until = Literal["blocked"]
Dir = Literal["left", "right"]
Sensor = Literal["front", "left", "right"]
SensorState = Literal["open", "blocked"]
Outcome = Literal["arrived", "splashed", "bumped", "ended", "loop", "tooLong"]
TeachKind = Literal["program", "clarify", "unmapped"]

MAX_STEPS = 8
MAX_BRANCH = 4
MAX_COUNT = 5
MAX_HEARD = 4
MAX_CANDIDATES = 4


def _normalize_node(node: BaseModel) -> None:
    """op 에 맞게 필수 필드를 확인하고, 쓰지 않는 필드는 null/빈 배열로 비운다."""
    op = node.op  # type: ignore[attr-defined]
    fields = type(node).model_fields
    if op != "move":
        node.count = None  # type: ignore[attr-defined]
        node.until = None  # type: ignore[attr-defined]
    elif node.until is not None:  # type: ignore[attr-defined]
        node.count = None  # type: ignore[attr-defined]
    if op == "turn" and node.dir is None:  # type: ignore[attr-defined]
        raise ValueError("turn_needs_dir")
    if op != "turn":
        node.dir = None  # type: ignore[attr-defined]
    if "sensor" in fields:
        if op == "if" and (node.sensor is None or node.state is None):  # type: ignore[attr-defined]
            raise ValueError("if_needs_sensor_state")
        if op != "if":
            node.sensor = None  # type: ignore[attr-defined]
            node.state = None  # type: ignore[attr-defined]
            node.then = []  # type: ignore[attr-defined]
            node.else_ = []  # type: ignore[attr-defined]
    if "body" in fields:
        if op == "repeat" and not node.body:  # type: ignore[attr-defined]
            raise ValueError("repeat_needs_body")
        if op != "repeat":
            node.body = []  # type: ignore[attr-defined]


class Leaf(CamelModel):
    op: LeafOp = Field(description="move 앞으로 / turn 제자리 돌기(이동 없음) / stop 멈춤")
    count: int | None = Field(
        default=None, ge=1, le=MAX_COUNT, description="move 칸 수 1~5. until 과 둘 다 null 이면 1칸"
    )
    until: Until | None = Field(default=None, description='"blocked" = 앞이 막히기 직전까지 쭉')
    dir: Dir | None = Field(default=None, description="turn 방향(필수)")

    @model_validator(mode="after")
    def _check(self) -> Leaf:
        _normalize_node(self)
        return self


class Inner(CamelModel):
    op: InnerOp
    count: int | None = Field(default=None, ge=1, le=MAX_COUNT)
    until: Until | None = None
    dir: Dir | None = None
    sensor: Sensor | None = Field(default=None, description="if 에서 볼 칸: front·left·right")
    state: SensorState | None = Field(default=None, description="if 조건: open·blocked")
    then: list[Leaf] = Field(default_factory=list, max_length=MAX_BRANCH)
    else_: list[Leaf] = Field(default_factory=list, max_length=MAX_BRANCH, alias="else")

    @model_validator(mode="after")
    def _check(self) -> Inner:
        _normalize_node(self)
        return self


class Step(CamelModel):
    op: StepOp
    count: int | None = Field(default=None, ge=1, le=MAX_COUNT)
    until: Until | None = None
    dir: Dir | None = None
    sensor: Sensor | None = None
    state: SensorState | None = None
    then: list[Leaf] = Field(default_factory=list, max_length=MAX_BRANCH)
    else_: list[Leaf] = Field(default_factory=list, max_length=MAX_BRANCH, alias="else")
    body: list[Inner] = Field(
        default_factory=list, max_length=MAX_BRANCH, description="repeat: 우체국에 갈 때까지 반복할 내용"
    )

    @model_validator(mode="after")
    def _check(self) -> Step:
        _normalize_node(self)
        return self


# --- POST /path/teach --------------------------------------------------------


class PendingClarify(CamelModel):
    question: str = Field(max_length=100)
    chosen: str = Field(max_length=60)


class PathTeachRequest(CamelModel):
    text: str = Field(min_length=1, max_length=200)
    program: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)
    map_id: str = Field(default="", max_length=40)
    attempt: int = Field(default=1, ge=0, le=1000)
    input_origin: InputOrigin = "adult"
    pending_clarify: PendingClarify | None = None


class Heard(CamelModel):
    phrase: str = Field(max_length=20, description="아이 말 조각")
    meaning: str = Field(max_length=30, description="티키가 알아들은 뜻")


class ClarifyOption(CamelModel):
    label: str = Field(max_length=30)
    program: list[Step] = Field(max_length=MAX_STEPS, description="이 뜻을 고르면 쓸 새 전체 프로그램")


class Clarify(CamelModel):
    question: str = Field(max_length=50)
    options: list[ClarifyOption] = Field(min_length=2, max_length=3)


class PathTeachResponse(AiMeta):
    source: Literal["ai", "fallback"] = Field(description="해석의 출처")
    kind: TeachKind = Field(description="program 새 프로그램 / clarify 되묻기 / unmapped 못 알아들음")
    program: list[Step] = Field(description="kind=program 이면 새 전체 프로그램, 아니면 요청 program 그대로")
    heard: list[Heard] = Field(description="티키가 알아들은 것(최대 4)")
    clarify: Clarify | None = Field(description="kind=clarify 일 때만")
    tiki_line: str = Field(max_length=60, description="움직이기 전 티키 한마디(또래 반말)")


# --- POST /path/react --------------------------------------------------------


class RunResult(CamelModel):
    outcome: Outcome
    moves: int = Field(ge=0, le=1000)
    stop_step_label: str | None = Field(default=None, max_length=40)
    previous_outcome: str | None = Field(default=None, max_length=20)
    changed_since_last: bool = False


class ChallengeCandidate(CamelModel):
    id: str = Field(min_length=1, max_length=40)
    summary: str = Field(max_length=60)


class PathReactRequest(CamelModel):
    text: str = Field(default="", max_length=200)
    program: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)
    map_id: str = Field(default="", max_length=40)
    attempt: int = Field(default=1, ge=0, le=1000)
    input_origin: InputOrigin = "adult"
    result: RunResult
    challenge_candidates: list[ChallengeCandidate] = Field(default_factory=list, max_length=MAX_CANDIDATES)


class PathReactResponse(AiMeta):
    source: Literal["ai", "fallback"] = Field(description="반응의 출처")
    tiki_line: str = Field(max_length=80, description="이번 시도에 대한 티키 반응(고칠 방법은 말하지 않음)")
    question: str | None = Field(max_length=50, description="아이 생각을 묻는 질문 1개")
    challenge_id: str | None = Field(description="challengeCandidates 중 하나. 후보가 없으면 null")
    challenge_line: str | None = Field(max_length=80, description="도전 지도 도발 한마디")


# --- LLM 구조화 출력(내부 전용) --------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LlmLeaf(_Strict):
    op: LeafOp
    count: int | None
    until: Until | None
    dir: Dir | None


class LlmInner(_Strict):
    op: InnerOp
    count: int | None
    until: Until | None
    dir: Dir | None
    sensor: Sensor | None
    state: SensorState | None
    then: list[LlmLeaf]
    else_: list[LlmLeaf] = Field(alias="else")


class LlmStep(_Strict):
    op: StepOp
    count: int | None
    until: Until | None
    dir: Dir | None
    sensor: Sensor | None
    state: SensorState | None
    then: list[LlmLeaf]
    else_: list[LlmLeaf] = Field(alias="else")
    body: list[LlmInner]


class LlmHeard(_Strict):
    phrase: str
    meaning: str


class LlmClarifyOption(_Strict):
    label: str
    program: list[LlmStep]


class LlmClarify(_Strict):
    question: str
    options: list[LlmClarifyOption]


class PathTeachLLM(_Strict):
    kind: TeachKind
    program: list[LlmStep]
    heard: list[LlmHeard]
    clarify: LlmClarify | None
    tiki_line: str


class PathReactLLM(_Strict):
    tiki_line: str
    question: str | None
    challenge_id: str | None
    challenge_line: str | None
