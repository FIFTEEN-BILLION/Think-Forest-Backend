"""첫 탐구(그림자) 사고력 엔진 스키마.

API 모델은 CamelModel(camelCase JSON). LLM 구조화 출력 모델은 일반 BaseModel 로 따로 둔다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import AiMeta, CamelModel

VariableId = Literal["lightHeight", "stickHeight", "distance", "brightness"]
EffectId = Literal["longer", "shorter", "same"]
ClaimEffectId = Literal["longer", "shorter", "same", "unknown"]
MissingId = Literal["evidence", "fairness", "variable", "direction"]
# example: 예시 버튼 그대로 / adult: 성인 테스터 입력 / child: 실제 아동 입력(child 모드에서만)
InputOrigin = Literal["example", "adult", "child"]
BeliefId = Literal["brightness_longer", "light_higher_longer", "light_irrelevant", "distance_irrelevant"]
ChallengeId = Literal["tall_stick", "far_light", "low_light_correct", "bright_same_correct", "confounded_claim"]


# --- 공통 조각 ---------------------------------------------------------------


class SetupIn(CamelModel):
    light_height: Literal["low", "mid", "high"]
    stick_height: Literal["short", "tall"]
    distance: Literal["near", "far"]
    brightness: Literal["dim", "bright"]

    def as_setup(self) -> dict[str, str]:
        return {
            "lightHeight": self.light_height,
            "stickHeight": self.stick_height,
            "distance": self.distance,
            "brightness": self.brightness,
        }


class ExperimentIn(CamelModel):
    base: SetupIn
    compare: SetupIn


class ClaimOut(CamelModel):
    variable: VariableId
    effect: ClaimEffectId


class InquiryMeta(AiMeta):
    source: Literal["ai", "fallback"] = Field(description="친구 대사·분석의 출처")


# --- GET /missions/shadow ----------------------------------------------------


class LevelOut(CamelModel):
    id: str
    label: str


class VariableOut(CamelModel):
    id: VariableId
    name: str
    up: str
    levels: list[LevelOut]


class TableRow(CamelModel):
    setup: dict[str, str]
    length: float


class BeliefOut(CamelModel):
    id: BeliefId
    line: str
    variable: VariableId
    claimed_effect: EffectId


class MissionResponse(CamelModel):
    id: str
    question: str
    variables: list[VariableOut]
    base_setup: dict[str, str]
    table: list[TableRow]
    design_feedback: dict[str, str]
    friend_beliefs: list[BeliefOut]
    truth: dict[str, str]
    facts: dict[str, str]
    model_note: str
    parent_question: str
    child_data_mode: Literal["demo", "child"]
    ai_available: bool


# --- POST /inquiry/interpret -------------------------------------------------


class InterpretRequest(CamelModel):
    prediction: ClaimEffectId
    reason: str = Field(default="", max_length=500)
    reason_skipped: bool = False
    input_origin: InputOrigin = "adult"


class InterpretResponse(InquiryMeta):
    claims: list[ClaimOut]
    uncertain: bool
    restatement: str
    friend_belief_id: BeliefId
    friend_line: str


# --- POST /inquiry/teach -----------------------------------------------------


class TeachRequest(CamelModel):
    belief_id: BeliefId
    message: str = Field(default="", max_length=500)
    cards: list[ExperimentIn] = Field(default_factory=list, max_length=5)
    attempt: int = Field(ge=1, le=10, description="이번이 몇 번째 가르치기인지(1부터)")
    input_origin: InputOrigin = "adult"


class TeachResponse(InquiryMeta):
    claim: ClaimOut | None
    uses_evidence: bool
    convinced: bool
    missing: MissingId | None
    help_level: Literal["probe", "hint", "explanation"] | None
    friend_reply: str


# --- POST /inquiry/challenge -------------------------------------------------


class ChallengeRequest(CamelModel):
    belief_id: BeliefId
    convinced: bool
    experiments: list[ExperimentIn] = Field(default_factory=list, max_length=10)
    final_text: str = Field(default="", max_length=500)
    final_reason: str = Field(default="", max_length=500)
    input_origin: InputOrigin = "adult"


class ChallengeOut(CamelModel):
    id: ChallengeId
    line: str
    base: dict[str, str]
    compare: dict[str, str]
    base_length: float
    compare_length: float
    friend_prediction: EffectId
    confounded: bool
    friend_correct: bool


class ChallengeResponse(InquiryMeta):
    challenge: ChallengeOut
    final_claims: list[ClaimOut]


# --- LLM 구조화 출력(내부 전용) --------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LlmClaim(_Strict):
    variable: VariableId
    effect: ClaimEffectId


class InterpretLLM(_Strict):
    claims: list[LlmClaim]
    uncertain: bool
    restatement: str
    friend_belief_id: BeliefId
    friend_line: str


class TeachLLM(_Strict):
    claim: LlmClaim | None
    uses_evidence: bool
    missing: Literal["evidence", "fairness", "variable", "direction", "none"]
    probe_reply: str
    convinced_reply: str


class ChallengeLLM(_Strict):
    final_claims: list[LlmClaim]
    challenge_id: ChallengeId
