"""POST /rubric/score — 되물음 채점(생각숲·실험실 공용)."""

from __future__ import annotations

from pydantic import Field

from .common import AiMeta, CamelModel, ChildContext, GuardianConsent


class RubricRequest(CamelModel):
    question: str = Field(description="아이에게 던진 되물음/질문")
    answer: str = Field(description="아이 답변 원문(발화 또는 타이핑)")
    child: ChildContext = Field(default_factory=ChildContext)
    consent: GuardianConsent | None = Field(
        default=None, description="있으면 마스킹 후 발화 저장, 없으면 저장하지 않음"
    )


class RubricResponse(AiMeta):
    observe: int = Field(ge=0, le=5, description="관찰 점수 0~5")
    reason: int = Field(ge=0, le=5, description="추론 점수 0~5")
    express: int = Field(ge=0, le=5, description="표현 점수 0~5")
    quote: str = Field(description="아이 말에서 그대로 따온 한 조각")
    followup: str = Field(description="아이 말을 인용한 다음 되물음")
    comment: str = Field(description="아이에게 보여줄 짧고 따뜻한 코멘트")
