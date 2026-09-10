"""여러 엔드포인트가 공유하는 요청·응답 조각.

API 계약(CLAUDE.md)의 키는 camelCase(ctrlLabel, followupIntensity 등)다.
파이썬 필드는 snake_case 로 쓰고 alias 로 camelCase 를 노출한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

FollowupIntensity = Literal["gentle", "normal", "deep"]
VocabLevel = Literal["easy", "normal", "rich"]


class CamelModel(BaseModel):
    """스키마 공통 베이스. JSON 은 camelCase, 파이썬은 snake_case."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ChildContext(CamelModel):
    """아이 프로필. 모든 생성·채점 프롬프트에 그대로 실려 나간다.

    생년월일·음성원본·사진은 담지 않는다(연령대만). 이름은 인사 표시용이며
    저장 단계에서 safety/pii.py 로 마스킹된다.
    """

    name: str | None = Field(default=None, description="아이 이름(인사 표시용, 저장 시 마스킹)")
    age_band: str | None = Field(default=None, description="연령 3구간 중 하나. 생년월일 아님")
    grade: str | None = Field(default=None, description="학년(선택)")
    interests: list[str] = Field(default_factory=list, description="관심사")
    followup_intensity: FollowupIntensity = Field(
        default="normal", description="되물음 강도(진단으로 자동 설정)"
    )
    vocab_level: VocabLevel = Field(default="normal", description="어휘 수준(진단으로 자동 설정)")


class GuardianConsent(CamelModel):
    """법정대리인 동의 상태. 없으면 아이 발화를 저장하지 않는다(체험은 허용)."""

    guardian: bool = False


class AiMeta(CamelModel):
    """모든 응답에 얹히는 정직성 메타. 프론트가 배지로 쓴다."""

    ai: bool = Field(description="실제 Claude 호출이면 true, 규칙 기반 폴백이면 false")
    error: str | None = Field(
        default=None, description="실패 시 프론트에 표시할 코드/사유. 성공이면 null"
    )
