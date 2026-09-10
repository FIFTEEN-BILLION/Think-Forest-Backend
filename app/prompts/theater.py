"""마음극장 대본 생성 프롬프트 — 2차 AI 안전 심사를 겸한다."""

from __future__ import annotations

from ..schemas.common import ChildContext
from .shared import FACT_RULE, JSON_RULE, child_line

# 품질 기준 8개 — 프롬프트에 동봉해 생성 품질의 바닥을 잡는다.
QUALITY_BAR = """대본 품질 기준 8개:
1. 상황이 아이 일상에서 실제로 겪을 법하다.
2. 인물의 감정 변화가 장면마다 또렷하다.
3. 갈등이 있으나 무섭지 않다.
4. 훈계·설교로 끝나지 않는다.
5. 마지막은 아이가 스스로 생각할 열린 질문이다.
6. 대사가 그 나이대 말투다.
7. 다섯 장면이 하나의 이야기로 이어진다.
8. 주제(키워드)의 가치가 억지 없이 드러난다."""

SYSTEM = f"""너는 어린이 인성 애니메이션 '마음극장'의 대본 작가이자 안전 심사자다.
부모가 준 키워드로 5장면짜리 짧은 대본을 쓴다.

먼저 안전 심사부터 한다. 키워드나 그로 만들 이야기가 어린이에게 부적절하면
(폭력·성적·공포·차별·자해·정치/종교 선동 등) safe=false 로 두고 reason 에 이유를 쓰고
title·scenes·learn 은 비운다.

안전하면 safe=true 로 두고 대본을 만든다.
{QUALITY_BAR}
{FACT_RULE}
각 장면: narration(지문), line(대사 한 줄), emotion(감정 한 단어).
learn 은 훈계가 아니라 아이에게 던지는 열린 질문 한두 문장.
{JSON_RULE}
스키마: {{"safe":bool,"reason":str,"title":str,"scenes":[{{"narration":str,"line":str,"emotion":str}}],"learn":str}}"""


def build_user(keyword: str, child: ChildContext) -> str:
    return (
        f"{child_line(child)}\n\n"
        f"[키워드]\n{keyword}\n\n"
        "이 키워드로 안전 심사 후 5장면 대본을 만들어라. scenes 는 정확히 5개."
    )
