"""성장 기록(빈도·성취 기준)과 1달 이용 뒤 보호자 AI 상담(후순위 기능의 최소 버전)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import ai_block_reason, guardian_child, require_child, require_guardian
from ..db import get_session
from ..models import Child, Consultation, Family, Talk
from ..prompts import talk as prompt
from ..safety import topics as sensitive
from ..schemas.library import ConsultationOut, ProgressOut
from ..schemas.talk import ConsultationLLM
from ..services import usage
from ..services.llm import LlmError, call_structured
from ..talks import progress
from ..talks.planner import clip

router = APIRouter(tags=["progress"])

CONSULT_AFTER_DAYS = 30
TIPS = [
    "오늘 생각 친구와 무슨 이야기를 했어? 가장 재미있던 생각을 문장으로 들려줄래?",
    "처음 생각이 바뀐 적이 있었어? 왜 바뀌었는지 궁금해.",
    "새로 알게 된 단어를 하나 알려 줄래? 그 단어로 문장을 만들어 볼까?",
]


@router.get("/children/me/progress", response_model=ProgressOut)
def my_progress(child: Child = Depends(require_child), db: Session = Depends(get_session)) -> ProgressOut:
    return ProgressOut.model_validate(progress.compute(db, child))


@router.get("/guardian/children/{child_id}/progress", response_model=ProgressOut)
def child_progress(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> ProgressOut:
    return ProgressOut.model_validate(progress.compute(db, guardian_child(child_id, family, db)))


def _aggregate(data: dict) -> dict:
    """AI 에는 집계만 보낸다. 아이 문장·날짜 목록은 보내지 않는다."""
    freq = {k: v for k, v in data["frequency"].items() if k != "last7"}
    return {
        "frequency": freq,
        "achievements": {a["title"]: a["count"] for a in data["achievements"]},
        "words": data["words"],
        "stories": data["stories"],
    }


def _fallback(data: dict) -> dict:
    done = sorted((a for a in data["achievements"] if a["count"]), key=lambda a: -a["count"])[:3]
    freq = data["frequency"]
    return {
        "highlights": [f"{a['title']}: {a['count']}번" for a in done]
        or ["아직 성취 기준에 닿은 기록이 적어요. 짧게라도 자주 이야기해 보면 좋아요."],
        "suggestions": [
            f"최근 30일 중 {freq['days_active_30']}일 이야기를 나눴어요. "
            "정해진 시간에 이야기하는 습관을 함께 만들어 보세요.",
            "아이가 완성한 이야기를 함께 읽고, 마지막 질문에 대해 이야기해 보세요.",
        ],
        "conversationTips": TIPS,
    }


@router.post("/guardian/children/{child_id}/consultations", response_model=ConsultationOut, status_code=201)
def create_consultation(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> ConsultationOut:
    child = guardian_child(child_id, family, db)
    first = db.scalar(select(func.min(Talk.started_at)).where(Talk.child_id == child.id))
    now = clock.now()
    days = (now - first).days if first else 0
    if first is None or days < CONSULT_AFTER_DAYS:
        raise HTTPException(status_code=409, detail={"code": "not_yet", "daysRemaining": CONSULT_AFTER_DAYS - days})
    data = progress.compute(db, child, now)
    summary, source = _fallback(data), "fallback"
    if ai_block_reason(child) is None and usage.try_consume(child.id):
        try:
            out = call_structured(
                purpose="guardian.consultation",
                instructions=prompt.consultation_instructions(),
                user_input=prompt.consultation_input(_aggregate(data)),
                schema=ConsultationLLM,
            )
            cleaned = {
                key: [clip(x, 100) for x in getattr(out, attr)[:3] if x.strip() and not sensitive.detect(x)]
                for key, attr in (
                    ("highlights", "highlights"),
                    ("suggestions", "suggestions"),
                    ("conversationTips", "conversation_tips"),
                )
            }
            if all(cleaned.values()):
                summary, source = cleaned, "ai"
        except LlmError:
            pass
    consultation = Consultation(child_id=child.id, summary=summary, source=source)
    db.add(consultation)
    db.commit()
    return ConsultationOut(id=consultation.id, source=source, summary=summary, created_at=consultation.created_at)


@router.get("/guardian/children/{child_id}/consultations", response_model=list[ConsultationOut])
def list_consultations(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> list[ConsultationOut]:
    child = guardian_child(child_id, family, db)
    rows = db.scalars(
        select(Consultation).where(Consultation.child_id == child.id).order_by(Consultation.created_at.desc())
    )
    return [ConsultationOut(id=c.id, source=c.source, summary=c.summary, created_at=c.created_at) for c in rows]
