"""v1 대화의 AI 사용 관문 — 테스트는 이 모듈의 `ai_block_reason`·`call_structured` 를 바꿔 끼운다.

아이 문장을 OpenAI 로 보내기 전에 반드시 `block_reason(child)` 를 확인한다.
ZDR 승인 전(`CHILD_DATA_MODE=demo`)에는 성인 테스터 계정이거나, **보호자가 현재 버전의 `ai_conversation` 동의를
남긴 프로필**일 때만 실제 AI 를 쓴다(사용자 결정). 동의가 없으면 막고, 호출하는 쪽이 규칙 기반 대사로 계속한다
(대화 자체는 절대 끊지 않는다). 기존 경로(`/talks`)는 `app/auth.py` 의 관문을 그대로 쓴다.
"""

from __future__ import annotations

from ..auth import ai_block_reason
from ..models import Child
from ..services import moderation, usage
from ..services.llm import LlmError, call_structured
from .models_accounts import child_has_ai_consent, guest_needs_consent
from .models_conversation import ConversationSession

MAX_AI_CALLS_PER_SESSION = 60
# 보호자 동의로 풀 수 있는 사유. 다른 사유(키 없음·스위치 꺼짐)는 동의와 상관없이 막는다.
CONSENT_UNLOCKS = "child_data_mode_off"

__all__ = ["LlmError", "allowed", "block_reason", "budget", "call", "moderate"]


def block_reason(child: Child) -> str | None:
    """막혔으면 사유 코드, 써도 되면 None. demo 모드는 테스터이거나 AI 대화 동의가 있을 때만 통과."""
    if guest_needs_consent(child):
        return "guest_consent_required"
    reason = ai_block_reason(child)
    if reason != CONSENT_UNLOCKS:
        return reason
    return None if child_has_ai_consent(child) else reason


def allowed(child: Child) -> bool:
    return block_reason(child) is None


def budget(child: Child, session: ConversationSession) -> str | None:
    """AI 를 한 번 써도 되면 None(호출 수를 센다), 아니면 막힌 사유 코드."""
    reason = block_reason(child)
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
