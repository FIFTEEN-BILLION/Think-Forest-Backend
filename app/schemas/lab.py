"""POST /lab/activity — 호기심 실험실 관찰 활동 생성."""

from __future__ import annotations

from pydantic import Field

from .common import AiMeta, CamelModel, ChildContext


class LabRequest(CamelModel):
    topic: str = Field(description="주제 문자열(프리셋 또는 아이가 입력한 키워드)")
    child: ChildContext = Field(default_factory=ChildContext)


class QuizItem(CamelModel):
    q: str = Field(description="문항")
    options: list[str] = Field(description="선택지")
    answer: int = Field(ge=0, description="정답 선택지 인덱스(0부터)")


class LabResponse(AiMeta):
    title: str = Field(description="활동 제목")
    ctrl_label: str = Field(description="파라메트릭 컨트롤 라벨(예: 빛의 각도)")
    ask: str = Field(description="양 끝을 관찰하게 만드는 되물음")
    concept: str = Field(description="관찰 뒤 읽어줄 개념 정리")
    quiz: list[QuizItem] = Field(default_factory=list, description="확인 문제")
