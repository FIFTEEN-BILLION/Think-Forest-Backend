"""첫 만남 대화 — 아이와 채팅하며 별명·소속+학년·좋아하는 것·키우고 싶은 것을 뽑는다.

학교 이름은 소속 종류만 남기고 버린다. 아이 원문은 저장하지 않고 뽑은 항목만 저장한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import ai_block_reason, require_child
from ..db import get_session
from ..models import Child, SafetyEvent
from ..prompts import talk as prompt
from ..safety import pii
from ..safety import topics as sensitive
from ..schemas.family import (
    ChildOut,
    OnboardingLLM,
    OnboardingMessage,
    OnboardingState,
    ProfileConfirmRequest,
    ProfileDraft,
)
from ..services import usage
from ..services.llm import LlmError, call_structured
from ..talks import onboarding as ob
from ..talks.planner import clip
from .families import child_out

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


def _profile(child: Child) -> dict:
    return {
        "nickname": child.nickname or None,
        "grade": child.grade,
        "affiliation": child.affiliation,
        "likes": list(child.likes or []),
        "want_to_learn": list(child.want_to_learn or []),
    }


def _state(child: Child, *, reply: str, ai: bool, error: str | None, notes: list[str]) -> OnboardingState:
    profile = _profile(child)
    missing = ob.missing_fields(profile)
    return OnboardingState(
        ai=ai,
        error=error,
        reply=reply,
        profile=ProfileDraft(**profile),
        missing=missing,
        done=not missing,
        notes=notes,
    )


def _merge_llm(profile: dict, out: OnboardingLLM, original: str) -> dict:
    merged = dict(profile)
    nickname = ob.clean_nickname(out.nickname)
    if nickname:
        merged["nickname"] = nickname
    # 학년·소속은 아이가 직접 말한 표현을 우선한다.
    grade = ob.parse_grade(original) or (out.grade if out.grade and 1 <= out.grade <= 6 else None)
    if grade:
        merged["grade"] = grade
    affiliation = ob.parse_affiliation(original) or (out.affiliation if out.affiliation in ob.AFFILIATIONS else None)
    if affiliation:
        merged["affiliation"] = affiliation
    for key in ("likes", "want_to_learn"):
        merged[key] = ob.clean_items([*(merged.get(key) or []), *getattr(out, key)])
    return merged


@router.get("", response_model=OnboardingState)
def current(child: Child = Depends(require_child)) -> OnboardingState:
    profile = _profile(child)
    missing = ob.missing_fields(profile)
    first = not (child.onboarding or {}).get("messages")
    if first and "nickname" in missing:
        reply = ob.QUESTIONS["intro"]
    else:
        reply = ob.question_for(missing[0] if missing else None, profile)
    return _state(child, reply=reply, ai=False, error=None, notes=[])


@router.post("/messages", response_model=OnboardingState)
def message(
    req: OnboardingMessage, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> OnboardingState:
    if child.profile_confirmed:
        raise HTTPException(status_code=409, detail="profile_already_confirmed")
    profile = _profile(child)
    missing = ob.missing_fields(profile)
    field = missing[0] if missing else None

    flag = sensitive.detect(req.text)
    if flag and flag.category != "personal_info":
        if flag.escalate:
            db.add(SafetyEvent(child_id=child.id, category=flag.category, escalate=True))
            db.commit()
        return _state(
            child, reply=f"{flag.redirect} {ob.question_for(field, profile)}", ai=False, error="redirected", notes=[]
        )

    masked = pii.mask(req.text, names=False)
    notes: list[str] = []
    if "학교·기관명" in masked.categories:
        notes.append("school_name_not_saved")
    if set(masked.categories) - {"학교·기관명"}:
        notes.append("personal_info_not_saved")

    ai, reply = False, None
    error = ai_block_reason(child)
    if error is None and not usage.try_consume(child.id):
        error = "daily_limit"
    if error is None:
        try:
            out = call_structured(
                purpose="onboarding.extract",
                instructions=prompt.onboarding_instructions(),
                user_input=prompt.onboarding_input(profile, missing, masked.text),
                schema=OnboardingLLM,
            )
            profile = _merge_llm(profile, out, req.text)
            ai = True
            candidate = clip(out.reply, 120)
            reply = candidate if candidate and not sensitive.detect(candidate) else None
        except LlmError as exc:
            error = f"ai_failed:{exc.code}"
    if not ai and field:
        profile = ob.apply_rules(profile, field, req.text, masked.text)

    child.nickname = profile.get("nickname") or ""
    child.grade = profile.get("grade")
    child.affiliation = profile.get("affiliation")
    child.likes = list(profile.get("likes") or [])
    child.want_to_learn = list(profile.get("want_to_learn") or [])
    child.onboarding = {**(child.onboarding or {}), "messages": int((child.onboarding or {}).get("messages", 0)) + 1}
    db.commit()

    remaining = ob.missing_fields(_profile(child))
    if reply is None:
        reply = ob.question_for(remaining[0] if remaining else None, _profile(child))
    return _state(child, reply=reply, ai=ai, error=None if ai else error, notes=notes)


@router.post("/confirm", response_model=ChildOut)
def confirm(
    req: ProfileConfirmRequest, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> ChildOut:
    if req.nickname is not None:
        nickname = ob.clean_nickname(req.nickname)
        if not nickname:
            raise HTTPException(status_code=422, detail="invalid_nickname")
        child.nickname = nickname
    if req.grade is not None:
        child.grade = req.grade
    if req.affiliation is not None:
        child.affiliation = req.affiliation
    if req.likes is not None:
        child.likes = ob.clean_items(req.likes)
    if req.want_to_learn is not None:
        child.want_to_learn = ob.clean_items(req.want_to_learn)
    missing = ob.missing_fields(_profile(child))
    if missing:
        raise HTTPException(status_code=422, detail={"code": "profile_incomplete", "missing": missing})
    child.profile_confirmed = True
    db.commit()
    return child_out(child)
