"""POST /report/summary — 이번 주 부모님께(성장 리포트 요약)."""

from __future__ import annotations

from pydantic import Field

from .common import AiMeta, CamelModel


class WeeklyScore(CamelModel):
    week: str = Field(description="주 라벨(예: 9월 1주)")
    observe: float = Field(ge=0, le=5)
    reason: float = Field(ge=0, le=5)
    express: float = Field(ge=0, le=5)


class ReportRequest(CamelModel):
    sentences: list[str] = Field(default_factory=list, description="아이가 말한 문장 원문 목록")
    weekly_scores: list[WeeklyScore] = Field(default_factory=list, description="주별 3축 점수")


class ReportResponse(AiMeta):
    summary: str = Field(description="아이 문장과 추이를 읽은 요약")
    next: str = Field(description="다음 주에 함께 해볼 것 제안")
