"""POST /theater/script — 마음극장 대본 생성. 폴백 템플릿 없음."""

from __future__ import annotations

from pydantic import Field

from .common import AiMeta, CamelModel, ChildContext


class ScriptRequest(CamelModel):
    keyword: str = Field(description="부모가 입력한 주제 키워드")
    child: ChildContext = Field(default_factory=ChildContext)


class Scene(CamelModel):
    narration: str = Field(description="장면 설명(지문)")
    line: str = Field(description="등장인물 대사")
    emotion: str = Field(description="대사에 실린 감정 한 단어")


class ScriptResponse(AiMeta):
    # AiMeta 가 ai: bool 과 error: str | None 을 제공한다. ai 는 필수.
    safe: bool = Field(description="1차 금칙어 + 2차 AI 심사를 모두 통과했는지")
    reason: str = Field(description="안전 판정 사유 또는 차단·실패 사유")
    title: str = Field(default="", description="대본 제목")
    scenes: list[Scene] = Field(default_factory=list, description="5개 장면")
    learn: str = Field(default="", description="훈계 없이 질문으로 맺는 열린 마무리")


class LibraryScript(CamelModel):
    """검수 대본 라이브러리 항목(TH-09). 생성 품질의 기준선."""

    id: str
    keyword: str = Field(description="가치 키워드(노인공경·공손함·정직·배려·용기·약속)")
    title: str
    scenes: list[Scene]
    learn: str
    parent_note: str = Field(description="부모용 해설")
