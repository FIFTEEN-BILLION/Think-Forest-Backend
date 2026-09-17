"""v1 대화의 AI 사용 관문 — 테스트는 이 모듈의 `ai_block_reason`·`call_structured` 를 바꿔 끼운다.

아이 문장을 OpenAI 로 보내기 전에 반드시 `ai_block_reason(child)` 를 확인한다(ZDR 전 demo 모드는 테스터만).
막히거나 실패하면 호출하는 쪽이 규칙 기반 대사로 계속한다.
"""

from __future__ import annotations

from ..auth import ai_block_reason
from ..models import Child
from ..services import moderation, usage
from ..services.llm import LlmError, call_structured
from .models_conversation import ConversationSession

MAX_AI_CALLS_PER_SESSION = 60

__all__ = ["LlmError", "allowed", "budget", "call", "moderate"]


def allowed(child: Child) -> bool:
    return ai_block_reason(child) is None


def budget(child: Child, session: ConversationSession) -> str | None:
    """AI 를 한 번 써도 되면 None(호출 수를 센다), 아니면 막힌 사유 코드."""
    reason = ai_block_reason(child)
    if reason:
        return reason
    if session.ai_calls >= MAX_AI_CALLS_PER_SESSION:
        return "session_call_limit"
    if not usage.try_consume(child.id):
        return "daily_limit"
    session.ai_calls += 1
    return None


def call(**kwargs):
    return call_structured(**kwargs)


def moderate(child: Child, masked: str) -> moderation.ModerationResult | None:
    if not allowed(child):
        return None
    return moderation.moderate(masked)
