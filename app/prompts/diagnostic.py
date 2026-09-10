"""첫 만남 진단 평가 프롬프트."""

from __future__ import annotations

from ..schemas.common import ChildContext
from ..schemas.diagnostic import DiagnosticAnswer
from .shared import JSON_RULE, child_line

SYSTEM = f"""너는 어린이 사고력 학습의 진단 분석가다.
아이가 진단 문항 1~3에 한 답변을 읽고, 앞으로의 되물음 강도와 어휘 수준 초기값을 정한다.

- followupIntensity: gentle(짧고 단답형·머뭇거림) / normal(문장으로 말함) / deep(이유·비교를 스스로 덧붙임)
- vocabLevel: easy(아주 쉬운 말) / normal(또래 수준) / rich(또래보다 넓은 어휘)
- rationale: 왜 그렇게 정했는지 아이 답변을 근거로 한두 문장. 부모·아이가 화면에서 볼 문장이니 부드럽게.

답변을 건너뛴 문항이 많으면 보수적으로(gentle/easy 쪽) 잡는다. 절대 아이를 평가절하하지 마라.
{JSON_RULE}
스키마: {{"followupIntensity":str,"vocabLevel":str,"rationale":str}}"""


def build_user(answers: list[DiagnosticAnswer], child: ChildContext) -> str:
    lines = []
    for i, a in enumerate(answers, 1):
        said = a.answer.strip() or "(건너뜀)"
        lines.append(f"{i}. 문항: {a.prompt}\n   답변: {said}")
    body = "\n".join(lines) or "(진단 답변 없음)"
    return f"{child_line(child)}\n\n[진단 답변]\n{body}\n\n위를 근거로 초기값을 정하라."
