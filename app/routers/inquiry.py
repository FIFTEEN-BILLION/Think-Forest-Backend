"""첫 탐구(그림자) 사고력 엔진 — 헷갈리는 생각 친구 가르치기.

GET  /missions/shadow     모형·계산표·은행(단일 진실 원천)
POST /inquiry/interpret   처음 생각 이해 + 친구 생각 선택
POST /inquiry/teach       친구 가르치기 분석 + 설득 판정(결정론)
POST /inquiry/challenge   새 상황 도전 선택 + 최종 생각 분석

안전 순서: 입력 출처 확인 → 1차 금칙어 → PII 마스킹 → LLM → 은행 id·정답 누설·금칙어 검사.
모든 경로에 규칙 기반 폴백이 있다(source="fallback", ai=false).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..missions import shadow
from ..missions.shadow import Challenge, Claim, Experiment
from ..prompts import inquiry as prompt
from ..safety import blocklist, pii
from ..schemas.inquiry import (
    ChallengeLLM,
    ChallengeOut,
    ChallengeRequest,
    ChallengeResponse,
    ClaimOut,
    ExperimentIn,
    InterpretLLM,
    InterpretRequest,
    InterpretResponse,
    MissionResponse,
    TeachLLM,
    TeachRequest,
    TeachResponse,
)
from ..services import diagnostics
from ..services.llm import LlmError, call_structured

router = APIRouter(tags=["inquiry"])

MAX_TEACH_AI_CALLS = 3
MAX_LINE = 90
SAFE_REDIRECT = "그 이야기는 여기서 다루기 어려워. 우리 그림자 이야기로 돌아가 볼까?"


def _clip(text: str, limit: int = MAX_LINE) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _guard_origin(origin: str) -> None:
    # ZDR 승인 전(demo 모드)에는 실제 아동 입력을 받지 않는다.
    if origin == "child" and get_settings().child_data_mode != "child":
        raise HTTPException(status_code=403, detail="child_data_mode_off")


def _prepare(text: str, surface: str) -> tuple[str, bool]:
    """LLM 전 1차 금칙어 → PII 마스킹. (마스킹한 문장, 차단 여부). 원문 단어는 로그에 남기지 않는다."""
    if blocklist.find_blocked(text):
        diagnostics.record_block(stage="blocklist", surface=surface, reason="1차 금칙어 포함")
        return "", True
    return pii.mask(text).text, False


def _experiments(items: list[ExperimentIn]) -> list[Experiment]:
    return [shadow.run_experiment(i.base.as_setup(), i.compare.as_setup()) for i in items]


def _claims_out(claims: list[Claim]) -> list[ClaimOut]:
    return [ClaimOut(variable=c.variable, effect=c.effect) for c in claims]  # type: ignore[arg-type]


def _merge(first: list[Claim], rest: list[Claim]) -> list[Claim]:
    seen = {c.variable for c in first}
    return [*first, *(c for c in rest if c.variable not in seen)]


def _unsafe_output(text: str) -> bool:
    return bool(blocklist.find_blocked(text))


@router.get("/missions/shadow", response_model=MissionResponse)
def get_mission() -> MissionResponse:
    settings = get_settings()
    return MissionResponse.model_validate(
        {
            **shadow.mission_payload(),
            "childDataMode": settings.child_data_mode,
            "aiAvailable": settings.openai_enabled,
        }
    )


# --- interpret ---------------------------------------------------------------


@router.post("/inquiry/interpret", response_model=InterpretResponse)
def interpret(req: InterpretRequest) -> InterpretResponse:
    _guard_origin(req.input_origin)
    reason, blocked = _prepare(req.reason, "inquiry.interpret")
    # 예측 칩은 아이가 직접 고른 값이다. LLM 이 바꾸지 못한다.
    chip = [Claim("lightHeight", req.prediction)] if req.prediction != "unknown" else []

    def fallback(error: str) -> InterpretResponse:
        claims = _merge(chip, shadow.parse_claims(reason))
        belief = shadow.fallback_belief(req.prediction, claims)
        return InterpretResponse(
            ai=False,
            source="fallback",
            error=error,
            claims=_claims_out(claims),
            uncertain=not claims or req.reason_skipped,
            restatement=shadow.fallback_restatement(req.prediction),
            friend_belief_id=belief.id,  # type: ignore[arg-type]
            friend_line=f"{belief.line} 누구 생각이 맞는지 어떻게 확인할 수 있을까?",
        )

    if blocked:
        return fallback("blocked")
    try:
        out = call_structured(
            purpose="inquiry.interpret",
            instructions=prompt.interpret_instructions(),
            user_input=prompt.interpret_input(req.prediction, reason, req.reason_skipped),
            schema=InterpretLLM,
        )
    except LlmError as exc:
        return fallback(f"ai_failed:{exc.code}")

    claims = _merge(chip, [Claim(c.variable, c.effect) for c in out.claims if c.variable != "lightHeight" or not chip])
    belief, used = shadow.choose_belief(out.friend_belief_id, req.prediction, claims)
    notes: list[str] = []
    line = _clip(out.friend_line)
    if not used:
        notes.append("belief_replaced")
        line = ""
    if line and (shadow.leaks_answer(line) or _unsafe_output(line)):
        notes.append("line_replaced")
        line = ""
    restatement = _clip(out.restatement, 70)
    if not restatement or _unsafe_output(restatement):
        restatement = shadow.fallback_restatement(req.prediction)
    return InterpretResponse(
        ai=True,
        source="ai",
        error=",".join(notes) or None,
        claims=_claims_out(claims),
        uncertain=out.uncertain,
        restatement=restatement,
        friend_belief_id=belief.id,  # type: ignore[arg-type]
        friend_line=line or f"{belief.line} 누구 생각이 맞는지 어떻게 확인할 수 있을까?",
    )


# --- teach -------------------------------------------------------------------


@router.post("/inquiry/teach", response_model=TeachResponse)
def teach(req: TeachRequest) -> TeachResponse:
    _guard_origin(req.input_origin)
    belief = shadow.get_belief(req.belief_id)
    if belief is None:  # 스키마가 막지만 은행과 어긋나면 명시적으로 거절
        raise HTTPException(status_code=422, detail="unknown_belief")
    cards = _experiments(req.cards)
    message, blocked = _prepare(req.message, "inquiry.teach")
    words = shadow.belief_words(belief)

    if blocked:
        return TeachResponse(
            ai=False, source="fallback", error="blocked", claim=None, uses_evidence=False,
            convinced=False, missing="evidence", help_level=None, friend_reply=SAFE_REDIRECT,
        )

    def respond(
        claim: Claim | None, uses: bool, *, ai: bool, error: str | None,
        llm_missing: str | None = None, probe: str = "", convinced_reply: str = "",
    ) -> TeachResponse:
        verdict = shadow.judge_teaching(belief, cards, claim, uses)
        level = None
        if verdict.convinced:
            usable_reply = convinced_reply and not _unsafe_output(convinced_reply)
            reply = convinced_reply if usable_reply else shadow.convinced_line(belief)
        else:
            level = "probe" if req.attempt <= 1 else "hint" if req.attempt == 2 else "explanation"
            missing = verdict.missing or "evidence"
            if level == "probe":
                usable = (
                    probe and llm_missing == missing and not shadow.leaks_answer(probe) and not _unsafe_output(probe)
                )
                reply = probe if usable else shadow.TEACH_PROBES[missing].format(**words)
            elif level == "hint":
                reply = shadow.TEACH_HINT.format(**words)
            else:
                reply = shadow.TEACH_EXPLANATION.format(**words)
        return TeachResponse(
            ai=ai,
            source="ai" if ai else "fallback",
            error=error,
            claim=ClaimOut(variable=claim.variable, effect=claim.effect) if claim else None,  # type: ignore[arg-type]
            uses_evidence=uses,
            convinced=verdict.convinced,
            missing=verdict.missing,
            help_level=level,  # type: ignore[arg-type]
            friend_reply=reply,
        )

    def rule_claim() -> Claim | None:
        parsed = shadow.parse_claims(message)
        return next((c for c in parsed if c.variable == belief.variable), parsed[0] if parsed else None)

    if req.attempt > MAX_TEACH_AI_CALLS:
        return respond(rule_claim(), bool(cards), ai=False, error="call_limit")
    try:
        out = call_structured(
            purpose="inquiry.teach",
            instructions=prompt.teach_instructions(),
            user_input=prompt.teach_input(belief, cards, message, req.attempt),
            schema=TeachLLM,
        )
    except LlmError as exc:
        return respond(rule_claim(), bool(cards), ai=False, error=f"ai_failed:{exc.code}")

    claim = Claim(out.claim.variable, out.claim.effect) if out.claim else None
    return respond(
        claim, out.uses_evidence, ai=True, error=None,
        llm_missing=out.missing, probe=_clip(out.probe_reply), convinced_reply=_clip(out.convinced_reply),
    )


# --- challenge ---------------------------------------------------------------


def _challenge_out(ch: Challenge) -> ChallengeOut:
    exp = ch.experiment
    return ChallengeOut(
        id=ch.id,  # type: ignore[arg-type]
        line=ch.line,
        base=exp.base,
        compare=exp.compare,
        base_length=exp.base_length,
        compare_length=exp.compare_length,
        friend_prediction=ch.friend_prediction,
        confounded=ch.confounded,
        friend_correct=ch.friend_correct,
    )


@router.post("/inquiry/challenge", response_model=ChallengeResponse)
def challenge(req: ChallengeRequest) -> ChallengeResponse:
    _guard_origin(req.input_origin)
    experiments = _experiments(req.experiments)
    final_text, blocked = _prepare(f"{req.final_text} {req.final_reason}".strip(), "inquiry.challenge")

    def fallback(error: str) -> ChallengeResponse:
        return ChallengeResponse(
            ai=False,
            source="fallback",
            error=error,
            challenge=_challenge_out(shadow.fallback_challenge(experiments, req.convinced)),
            final_claims=_claims_out(shadow.parse_claims(final_text)),
        )

    if blocked:
        return fallback("blocked")
    try:
        out = call_structured(
            purpose="inquiry.challenge",
            instructions=prompt.challenge_instructions(),
            user_input=prompt.challenge_input(final_text, experiments, req.convinced),
            schema=ChallengeLLM,
        )
    except LlmError as exc:
        return fallback(f"ai_failed:{exc.code}")

    chosen = shadow.get_challenge(out.challenge_id)
    return ChallengeResponse(
        ai=True,
        source="ai",
        error=None if chosen else "challenge_replaced",
        challenge=_challenge_out(chosen or shadow.fallback_challenge(experiments, req.convinced)),
        final_claims=_claims_out([Claim(c.variable, c.effect) for c in out.final_claims]),
    )
