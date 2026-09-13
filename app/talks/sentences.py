"""답은 문장으로 — 단답이면 다음 질문으로 넘어가지 않고 문장으로 다시 말하게 한다.

글자 수 난이도가 아니다. '문장인지'만 본다. 길게 쓰는 건 아이 자유다.
받아쓰기(음성)는 문장부호가 없을 수 있어 어절 수로도 인정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ENDING = re.compile(
    r"(다|요|어|아|야|지|네|래|까|니|죠|음|함|거든|는데|해|대|자|걸|게|데|고|면|서|며|듯|봐)[.!?~…]*$"
)
_UNSURE = re.compile(r"^(몰라|모르겠|글쎄|음+|그냥)")
_REASON = re.compile(r"왜냐하면|때문|그래서|니까|거든|라서|해서|어서|아서")


@dataclass(frozen=True)
class SentenceCheck:
    ok: bool
    reason: str | None  # empty|unsure|too_short


def check_sentence(text: str) -> SentenceCheck:
    t = " ".join((text or "").split())
    if not t:
        return SentenceCheck(False, "empty")
    words, chars = len(t.split()), len(t.replace(" ", ""))
    if (words >= 3 and chars >= 8 and _ENDING.search(t)) or (words >= 5 and chars >= 12):
        return SentenceCheck(True, None)
    if _UNSURE.match(t):
        return SentenceCheck(False, "unsure")
    return SentenceCheck(False, "too_short")


def sentence_count(text: str) -> int:
    parts = [p for p in re.split(r"[.!?\n。]+", text or "") if len(p.split()) >= 2]
    words = len((text or "").split())
    if len(parts) <= 1 and words >= 12:
        return 2 + (words - 12) // 8
    return len(parts)


def has_reason(text: str) -> bool:
    return bool(_REASON.search(text or ""))


EXPAND_LINES: dict[str, str] = {
    "empty": "네 생각을 문장으로 들려줘!",
    "too_short": "좋아! 그 생각을 문장으로 조금 더 길게 말해 줄래? '왜냐하면'을 붙여 봐도 좋아.",
    "unsure": "모르는 건 괜찮아. 어떤 부분이 헷갈리는지, 아니면 '아마 ~일 것 같아'처럼 문장으로 말해 줄래?",
}

STARTERS: dict[str, list[str]] = {
    "default": ["나는 ___라고 생각해.", "왜냐하면 ___ 때문이야.", "예를 들면 ___."],
    "unsure": ["나는 ___ 부분이 헷갈려.", "아마 ___일 것 같아. 왜냐하면 ___."],
    "imagine": ["내가 그곳에 있다면 ___.", "그때 ___ 소리가 들리고 ___가 보여."],
    "reason_check": ["나는 처음 생각을 그대로 믿어. 왜냐하면 ___.", "나는 생각을 바꿀래. 왜냐하면 ___."],
    "compose": ["처음에 나는 ___라고 생각했어.", "이야기하면서 ___를 알게 됐어.", "그래서 지금은 ___라고 생각해."],
}


def starters_for(move: str, reason: str | None = None) -> list[str]:
    if reason == "unsure":
        return STARTERS["unsure"]
    return STARTERS.get(move, STARTERS["default"])
