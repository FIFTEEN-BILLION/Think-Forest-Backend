"""POST /diagnostic/assess — 첫 만남 진단 결과."""

from __future__ import annotations

import re

from fastapi import APIRouter

from ..prompts import diagnostic as prompt
from ..schemas.diagnostic import DiagnosticRequest, DiagnosticResponse
from ..services.claude import ClaudeError, call_json

router = APIRouter(prefix="/diagnostic", tags=["diagnostic"])

_REASON_MARKERS = (
    "왜냐", "때문", "그래서", "만약", "그러면", "라서", "처럼", "같이", "보다",
    "니까", "려고", "위해", "든지", "거든", "면서", "지만",
)


def _fallback(req: DiagnosticRequest) -> DiagnosticResponse:
    """규칙 기반 폴백 — 답변 분량·표지어·건너뛴 수로 초기값을 잡는다."""
    said = [a.answer.strip() for a in req.answers if a.answer.strip()]
    skipped = len(req.answers) - len(said)
    joined = " ".join(said)
    words = [w for w in re.split(r"\s+", joined) if w]
    avg_len = (len(joined) / len(said)) if said else 0
    reason_hits = sum(joined.count(m) for m in _REASON_MARKERS)
    question_hits = joined.count("?") + joined.count("궁금") + joined.count("왜")
    uniq_ratio = (len(set(words)) / len(words)) if words else 0

    if not said or skipped >= 2 or avg_len < 8:
        intensity, vocab = "gentle", "easy"
    elif (reason_hits >= 2 or (reason_hits >= 1 and question_hits >= 1)) and avg_len >= 20:
        intensity, vocab = "deep", "rich" if uniq_ratio > 0.7 else "normal"
    else:
        intensity, vocab = "normal", "normal"

    if not said:
        rationale = "진단 답변이 없어 가장 부드러운 설정으로 시작해요. 함께하다 보면 자동으로 맞춰집니다."
    else:
        rationale = (
            f"답변 {len(said)}개를 살펴보니 "
            + ("이유를 스스로 덧붙이는 편이라" if reason_hits >= 2 else "또박또박 말하는 편이라")
            + f" 되물음 강도는 '{intensity}', 어휘 수준은 '{vocab}'으로 시작해요."
        )
    return DiagnosticResponse(
        ai=False,
        error="AI 미연결 — 규칙 기반 임시 설정",
        followup_intensity=intensity,
        vocab_level=vocab,
        rationale=rationale,
    )


@router.post("/assess", response_model=DiagnosticResponse)
def assess(req: DiagnosticRequest) -> DiagnosticResponse:
    try:
        data = call_json(
            purpose="diagnostic.assess",
            system=prompt.SYSTEM,
            user=prompt.build_user(req.answers, req.child),
            max_tokens=500,
        )
        return DiagnosticResponse(
            ai=True,
            followup_intensity=data.get("followupIntensity") or data["followup_intensity"],
            vocab_level=data.get("vocabLevel") or data["vocab_level"],
            rationale=data["rationale"],
        )
    except ClaudeError as exc:
        resp = _fallback(req)
        resp.error = f"AI 실패({exc.code}) — 규칙 기반 폴백"
        return resp
    except Exception as exc:  # noqa: BLE001
        resp = _fallback(req)
        resp.error = f"AI 응답 형식 오류({type(exc).__name__}) — 규칙 기반 폴백"
        return resp
