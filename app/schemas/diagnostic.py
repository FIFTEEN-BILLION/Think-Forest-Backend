"""POST /diagnostic/assess — 첫 만남 진단 결과(구조도 DG-01~02).

진단 문항 1~3의 답변을 읽어 되물음 강도와 어휘 수준을 정하고 근거를 남긴다.
생년월일·이름은 받지 않는다.
"""

from __future__ import annotations

from pydantic import Field

from .common import AiMeta, CamelModel, ChildContext, FollowupIntensity, VocabLevel


class DiagnosticAnswer(CamelModel):
    prompt: str = Field(description="진단 문항")
    answer: str = Field(default="", description="아이 답변. 건너뛰면 빈 문자열")


class DiagnosticRequest(CamelModel):
    answers: list[DiagnosticAnswer] = Field(default_factory=list)
    child: ChildContext = Field(default_factory=ChildContext)


class DiagnosticResponse(AiMeta):
    followup_intensity: FollowupIntensity = Field(description="되물음 강도 초기값")
    vocab_level: VocabLevel = Field(description="어휘 수준 초기값")
    rationale: str = Field(description="이렇게 설정한 근거 한두 문장(화면에 노출)")
