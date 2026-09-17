"""한국어 조사·호칭 도우미 — 티키 대사를 자연스럽게 만든다(별이는, 별아, 공룡을)."""

from __future__ import annotations


def has_batchim(word: str) -> bool:
    """마지막 한글 글자에 받침이 있으면 True. 한글이 아니면 False."""
    for ch in reversed((word or "").strip()):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isalnum():
            return False
    return False


def josa(word: str, with_batchim: str, without: str) -> str:
    return f"{word}{with_batchim if has_batchim(word) else without}"


def call_name(nickname: str | None) -> str:
    """문장 속에서 부르는 이름: 별 → 별이, 민지 → 민지. 없으면 '너'."""
    if not nickname:
        return "너"
    return josa(nickname, "이", "") if any("가" <= c <= "힣" for c in nickname) else nickname


def vocative(nickname: str | None) -> str:
    """부르는 말: 별 → 별아, 민지 → 민지야."""
    if not nickname:
        return "친구야"
    return josa(nickname, "아", "야")
