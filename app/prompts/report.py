"""성장 리포트 요약 프롬프트."""

from __future__ import annotations

from ..schemas.report import WeeklyScore
from .shared import JSON_RULE

SYSTEM = f"""너는 어린이 사고력 학습 서비스가 학부모에게 보내는 주간 편지를 쓴다.
아이가 실제로 말한 문장들과 주별 3축 점수 추이를 읽고,

- summary: 이번 주 아이의 생각이 어디서 자랐는지. 아이 문장을 근거로 인용. 3~5문장.
  숫자 나열 대신 아이가 한 말에서 관찰되는 변화를 이야기하라.
- next: 다음 주에 가정에서 5분이면 해볼 수 있는 구체적 제안 하나.

과장하지 말고, 점수가 낮으면 낮은 대로 따뜻하게 짚어라.
{JSON_RULE}
스키마: {{"summary":str,"next":str}}"""


def build_user(sentences: list[str], weekly_scores: list[WeeklyScore]) -> str:
    said = "\n".join(f"- {s}" for s in sentences) or "(수집된 문장 없음)"
    trend = (
        "\n".join(
            f"- {w.week}: 관찰 {w.observe} / 추론 {w.reason} / 표현 {w.express}"
            for w in weekly_scores
        )
        or "(점수 없음)"
    )
    return f"[아이가 말한 문장]\n{said}\n\n[주별 점수]\n{trend}\n\n위를 읽고 편지를 써라."
