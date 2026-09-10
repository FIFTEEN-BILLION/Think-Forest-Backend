"""POST /rubric/score — 되물음 채점."""

from __future__ import annotations

import re

from fastapi import APIRouter

from ..prompts import rubric as prompt
from ..schemas.rubric import RubricRequest, RubricResponse
from ..services import storage
from ..services.claude import ClaudeError, call_json

router = APIRouter(prefix="/rubric", tags=["rubric"])

_REASON_MARKERS = ("왜냐", "때문", "그래서", "만약", "아마", "그러면", "라서")
_OBSERVE_MARKERS = ("봤", "보니", "들었", "느꼈", "색", "소리", "모양", "냄새")


def _fallback(req: RubricRequest) -> RubricResponse:
    """규칙 기반 폴백 — 분량과 표지어로 대략의 점수를 낸다. 정직하게 ai=false."""
    answer = req.answer.strip()
    words = [w for w in re.split(r"\s+", answer) if w]
    n = len(words)

    express = 1 if n < 3 else 2 if n < 8 else 3 if n < 20 else 4
    observe = 2 + sum(1 for m in _OBSERVE_MARKERS if m in answer)
    reason = 1 + sum(1 for m in _REASON_MARKERS if m in answer) * 2
    observe, reason = min(observe, 5), min(reason, 5)

    quote = answer[:40] + ("…" if len(answer) > 40 else "") if answer else ""
    followup = (
        f'"{quote}" 라고 했는데, 그렇게 생각한 이유를 조금 더 말해 줄래?'
        if quote
        else "방금 떠오른 생각을 한 문장으로 말해 볼까?"
    )
    return RubricResponse(
        ai=False,
        error="AI 미연결 — 규칙 기반 임시 채점",
        observe=observe,
        reason=reason,
        express=express,
        quote=quote,
        followup=followup,
        comment="네 생각을 말해 줘서 좋았어. 다음엔 '왜 그런지'도 같이 말해 보자.",
    )


@router.post("/score", response_model=RubricResponse)
def score(req: RubricRequest) -> RubricResponse:
    # 보호자 동의가 있으면 마스킹 후 발화 저장. 없으면 저장하지 않는다(체험은 계속).
    storage.save_utterance(
        req.answer, guardian_consent=bool(req.consent and req.consent.guardian)
    )

    try:
        data = call_json(
            purpose="rubric.score",
            system=prompt.SYSTEM,
            user=prompt.build_user(req.question, req.answer, req.child),
        )
        return RubricResponse(ai=True, **data)
    except ClaudeError as exc:
        resp = _fallback(req)
        resp.error = f"AI 실패({exc.code}) — 규칙 기반 폴백"
        return resp
    except Exception as exc:  # noqa: BLE001 — 스키마 불일치 등도 폴백으로
        resp = _fallback(req)
        resp.error = f"AI 응답 형식 오류({type(exc).__name__}) — 규칙 기반 폴백"
        return resp
