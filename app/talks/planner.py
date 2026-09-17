"""대화 흐름(결정론). AI 는 문장만 만들고, 어떤 질문을 던질지는 여기서 정한다.

흐름: 주제 던지기 → 꼬리질문 → 생활 연결 → '진짜일까?' → 상상 장면 → 꼬리질문
     → 생각 지키기/바꾸기(이유 다시 말하기) → (시간이 차면) 긴 문장으로 정리 → 이야기 완성
정리 전까지 시간이 남으면 꼬리질문·연결·반례·상상을 돌아가며 이어 간다.
한 이야기는 실제 대화 시간 15분이 필수이고, 원하면 더 이어 갈 수 있다.
"""

from __future__ import annotations

from datetime import datetime

MOVES: tuple[str, ...] = ("hook", "tail", "connect", "challenge", "imagine", "reason_check", "compose", "continue")
SEQUENCES: dict[str, tuple[str, ...]] = {
    "topic": ("tail", "connect", "challenge", "imagine", "tail", "reason_check"),
    "diary": ("tail", "connect", "challenge", "imagine", "reason_check"),
}
CYCLE: tuple[str, ...] = ("tail", "connect", "challenge", "imagine")
COMPOSE_AFTER_SECONDS = 720  # 12분이 지나면 정리할 타이밍을 준다
MAX_EXTRA_TURNS = 4  # 시간이 모자라도 이만큼 더 이야기하면 정리로 넘어간다
TURN_GAP_CAP_SECONDS = 180  # 자리를 비운 시간은 대화 시간으로 치지 않는다


def next_move(mode: str, accepted: int, active_seconds: int, composed: bool) -> str:
    """accepted: 지금까지 문장으로 답한 수(이번 답 포함)."""
    if composed:
        return "continue"
    seq = SEQUENCES.get(mode, SEQUENCES["topic"])
    if accepted <= len(seq):
        return seq[max(accepted, 1) - 1]
    extra = accepted - len(seq)
    if active_seconds >= COMPOSE_AFTER_SECONDS or extra > MAX_EXTRA_TURNS:
        return "compose"
    return CYCLE[(extra - 1) % len(CYCLE)]


def active_delta(last_turn_at: datetime, now: datetime) -> int:
    return int(min(max((now - last_turn_at).total_seconds(), 0), TURN_GAP_CAP_SECONDS))


def hook_line(nickname: str, theme_title: str, topic: dict) -> str:
    hello = f"안녕, {nickname}! " if nickname else "안녕! "
    return f"{hello}오늘은 {theme_title}이야. {topic['hook']}"


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fallback_reaction(child_text: str) -> str:
    return f"“{clip(child_text, 24)}”라고 생각했구나."


def fallback_question(move: str, topic: dict) -> str:
    generic = {
        "tail": "왜 그렇게 생각했어? 네 이유를 예를 들어서 문장으로 들려줘.",
        "connect": topic.get("connect") or "이 생각을 우리 생활 어디에서 볼 수 있을까?",
        "challenge": topic.get("challenge") or "정말 언제나 그럴까? 아닐 수도 있는 경우를 떠올려 볼래?",
        "imagine": topic.get("imagine") or "그 장면 속에 네가 들어갔다고 상상해 보자. 무엇이 보여?",
        "reason_check": (
            "처음 생각을 그대로 믿을래, 아니면 바꾸고 싶어? 어느 쪽이든 괜찮아. 그 이유를 문장으로 말해 줘."
        ),
        "compose": (
            "이제 지금까지 한 이야기를 세 문장 이상으로 정리해 볼까? "
            "처음 생각, 새로 알게 된 것, 지금 생각을 차례로 말해 줘."
        ),
        "continue": "더 상상해 보고 싶은 게 있으면 계속 이야기해도 좋아. 무엇이 더 궁금해?",
        "hook": topic.get("hook", ""),
    }
    return generic[move]
