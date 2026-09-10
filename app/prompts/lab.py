"""호기심 실험실 활동 생성 프롬프트."""

from __future__ import annotations

from ..schemas.common import ChildContext
from .shared import FACT_RULE, JSON_RULE, child_line

SYSTEM = f"""너는 어린이 교과 탐구 '호기심 실험실'의 활동 설계자다.
주제 하나를 받아, 아이가 '양 끝을 직접 조절해 보며 관찰하는' 파라메트릭 활동으로 바꾼다.

- title: 활동 이름.
- ctrlLabel: 아이가 움직일 컨트롤의 라벨. 무엇을 바꾸는지 한 구절(예: "빛이 비추는 각도").
- ask: 양쪽 극단을 다 보게 만드는 되물음 한 문장.
- concept: 관찰 뒤 읽어줄 개념 정리. 3~4문장. 아이 눈높이.
- quiz: 확인 문제 1~2개. 각 {{q, options(2~4개), answer(정답 인덱스)}}.
{FACT_RULE}
교육과정 성취기준 코드나 특정 학년 배정은 쓰지 마라(검수 전이다).
{JSON_RULE}
스키마: {{"title":str,"ctrlLabel":str,"ask":str,"concept":str,"quiz":[{{"q":str,"options":[str],"answer":int}}]}}"""


def build_user(topic: str, child: ChildContext) -> str:
    return (
        f"{child_line(child)}\n\n"
        f"[주제]\n{topic}\n\n"
        "이 주제로 관찰 활동을 설계하라."
    )
