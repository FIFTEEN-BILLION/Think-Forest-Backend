"""성장 리포트·보호자 상담 프롬프트. 말하기·안전 규칙은 기존 대화 프롬프트(`prompts/talk.py`)와 같은 것을 쓴다.

AI 에는 **집계와 아이가 실제로 한 말 인용만** 보낸다. 점수·발달 진단 표현은 금지한다.
"""

from __future__ import annotations

from ..prompts.talk import base_rules

_NO_DIAGNOSIS = """
[반드시 지킬 것]
- 보호자에게 드리는 글이므로 존댓말로 쓴다.
- 점수·등수·수준·발달 단계·진단처럼 말하지 않는다. "또래보다", "부족합니다", "우수합니다" 같은 말을 쓰지 않는다.
- 받은 집계와 인용에 없는 성향·감정·가정 형편을 지어내지 않는다.
- 각 항목은 한 문장, 100자 이내. 항목 수는 지시한 개수를 넘지 않는다.
"""


def summary_instructions() -> str:
    return (
        base_rules(character="티키", audience="초등학교 2~4학년(8~10살)")
        + _NO_DIAGNOSIS
        + """
[지금 할 일: 선택한 기간의 서술형 요약]
- highlights: 기간 동안 관찰된 활동에서 이어지고 있는 점 최대 3개.
- suggestions: 집에서 아이와 해 볼 만한 것 최대 3개.
- conversation_tips: 아이에게 그대로 건넬 수 있는 질문 예시 최대 3개.
"""
    )


def summary_input(counts: dict, quotes: list[str], titles: list[str]) -> str:
    return (
        f"[기간 집계] {counts}\n"
        f"[아이가 만든 이야기 제목] {titles}\n"
        f"[아이가 실제로 한 말(인용)] {quotes}\n"
        "위 자료에 없는 내용은 쓰지 않는다."
    )


def consultation_instructions() -> str:
    return (
        base_rules(character="티키", audience="초등학교 2~4학년(8~10살)")
        + _NO_DIAGNOSIS
        + """
[지금 할 일: 보호자 월간 상담(관찰 기록)]
- observed_behaviors: 한 달 대화에서 관찰된 행동 최대 3개. 반드시 받은 집계·인용에 근거한다.
- examples: 그 행동이 드러난 대표 사례 최대 3개. 아이 말 인용을 그대로 쓴다.
- questions_to_try: 보호자가 아이와 함께 해 볼 질문 최대 3개.
"""
    )


def consultation_input(period: str, counts: dict, quotes: list[str], titles: list[str]) -> str:
    return (
        f"[기간] {period}\n[집계] {counts}\n[이야기 제목] {titles}\n[아이가 실제로 한 말(인용)] {quotes}\n"
        "위 자료에 없는 내용은 쓰지 않는다."
    )


def answer_instructions() -> str:
    return (
        base_rules(character="티키", audience="초등학교 2~4학년(8~10살)")
        + _NO_DIAGNOSIS
        + """
[지금 할 일: 상담을 읽은 보호자의 후속 질문에 답하기]
- answer: 200자 이내 한 문단. 상담 기록에 있는 관찰만 근거로 답한다.
- 모르면 모른다고 적고, 대신 아이와 확인해 볼 방법을 제안한다.
- 의료·심리·발달 상담을 대신하지 않는다고 분명히 한다.
"""
    )


def answer_input(consultation: dict, question: str) -> str:
    return f"[상담 기록] {consultation}\n[보호자 질문] {question}"
