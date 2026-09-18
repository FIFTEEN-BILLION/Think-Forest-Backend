"""v1 책장 프롬프트 — 이야기책 머리말.

말하기·안전 규칙은 티키 대화(`prompts.v1_conversation.TIKI_BASE`)를 그대로 쓴다. 낱말 뜻풀이는 기존
`prompts.talk.words_instructions()` 를 그대로 재사용하므로 여기에 다시 적지 않는다.
"""

from __future__ import annotations

from ..prompts.v1_conversation import TIKI_BASE


def book_intro_instructions() -> str:
    return (
        TIKI_BASE
        + """
[지금 할 일: 아이가 묶은 이야기책의 머리말]
- introduction: 이 책을 펼칠 사람에게 건네는 2~3문장. 120자 이내.
- 제목과 이야기 제목·요약에 있는 것만 쓴다. 없는 내용을 지어내지 않는다.
- 아이가 생각한 과정을 알아주는 말로 쓴다. 점수·등수·과한 칭찬은 쓰지 않는다.
- 아이 이름·학교·사는 곳은 쓰지 않는다.
"""
    )


def book_intro_input(title: str, stories: list[tuple[str, str]]) -> str:
    lines = "\n".join(f"- {t}: {s}" for t, s in stories) or "- (아직 담긴 이야기가 없다)"
    return f"[책 제목] {title}\n[담긴 이야기]\n{lines}"
