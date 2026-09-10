"""프롬프트 공통 조각."""

from __future__ import annotations

from ..schemas.common import ChildContext

# 모든 생성 프롬프트에 공통으로 박는 사실성·안전 규칙.
FACT_RULE = (
    "확인되지 않은 사실을 넣지 마라. 교육과정 코드·연도·통계 수치처럼 출처가 필요한 것은 "
    "쓰지 말고, 대신 관찰과 개념 설명으로 채워라. 모르면 모른다고 하라."
)

SAFETY_RULE = (
    "대상은 어린이다. 폭력·성적 표현·차별·공포 조장·자해 언급을 넣지 마라. "
    "훈계조로 끝내지 말고 질문으로 열어 두어라."
)

JSON_RULE = "설명 없이 요청한 JSON 객체 하나만 출력하라. 코드펜스도 붙이지 마라."


def child_line(child: ChildContext) -> str:
    parts: list[str] = []
    if child.age_band:
        parts.append(f"연령대 {child.age_band}")
    if child.grade:
        parts.append(f"학년 {child.grade}")
    if child.interests:
        parts.append("관심사 " + ", ".join(child.interests))
    parts.append(f"되물음 강도 {child.followup_intensity}")
    parts.append(f"어휘 수준 {child.vocab_level}")
    return "아이 정보: " + " · ".join(parts) if parts else "아이 정보 없음"
