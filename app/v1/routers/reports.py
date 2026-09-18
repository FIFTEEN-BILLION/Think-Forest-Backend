"""v1 나의 발자국·성장 리포트(명세 18절)와 보호자 월간 AI 상담(명세 29절).

리포트에는 점수·등급·발달 진단이 없다. 관찰된 활동 횟수와 아이 말 인용만 담는다.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import cursor, idempotency, report_engine
from ..deps import ProfileScope, require_profile
from ..errors import ApiError
from ..models_social import ConsultationQuestion, GuardianConsultation, ReportSummary
from ..report_schemas import (
    ConsultationBody,
    ConsultationCreate,
    ConsultationList,
    ConsultationOut,
    ConsultationQuestionCreate,
    ConsultationQuestionResponse,
    ConsultationResponse,
    ConsultationSummary,
    EligibilityResponse,
    Period,
    ProgressResponse,
    SummaryCreate,
    SummaryResponse,
)

router = APIRouter(tags=["v1-reports"])
MAX_SUMMARY_DAYS = 180


def _check_profile(scope: ProfileScope, wanted: str | None) -> None:
    """본문의 profileId 도 URL 처럼 믿지 않는다. 내 프로필이 아니면 있는지조차 알려 주지 않는다."""
    if wanted and wanted != scope.profile_id:
        raise ApiError(404, "PROFILE_NOT_FOUND", "아이 프로필을 찾을 수 없어요.", {"profileId": wanted})


def _check_reports_permission(scope: ProfileScope) -> None:
    if "VIEW_REPORTS" not in scope.permissions:
        raise ApiError(403, "FORBIDDEN", "리포트를 볼 권한이 없어요.")


@router.get("/reports/progress", response_model=ProgressResponse)
def get_progress(
    period: Period = Query(default="30d"),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    _check_reports_permission(scope)
    return report_engine.progress(db, scope.user.id, scope.profile_id, period)


# --- 서술형 요약 ----------------------------------------------------------------


@router.post("/reports/summaries", response_model=SummaryResponse, status_code=201)
def create_summary(
    req: SummaryCreate,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    _check_reports_permission(scope)
    _check_profile(scope, req.profile_id)
    route = "POST /reports/summaries"
    if (replayed := idempotency.replay(db, scope.user.id, idempotency_key, route)) is not None:
        return replayed
    today = report_engine.kst_date(clock.now())
    to_day = report_engine.parse_day(req.to, "to") or today
    from_day = report_engine.parse_day(req.from_, "from") or (to_day - timedelta(days=6))
    if from_day > to_day or (to_day - from_day).days >= MAX_SUMMARY_DAYS:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["from", "to"]})

    records = report_engine.collect(db, scope.user.id, from_day, to_day)
    version = records.source_version
    rows = list(
        db.scalars(
            select(ReportSummary).where(
                ReportSummary.profile_id == scope.profile_id,
                ReportSummary.period_from == from_day.isoformat(),
                ReportSummary.period_to == to_day.isoformat(),
            )
        )
    )
    if same := next((r for r in rows if r.source_version == version), None):
        # 같은 기간 + 같은 원본 버전이면 새로 만들지 않는다.
        same.status = "CURRENT"
        body = SummaryResponse(summary=report_engine.summary_out(same), reused=True)
        db.commit()
        return body
    for row in rows:  # 원본이 달라졌으니 이전 요약은 STALE.
        row.status = "STALE"

    generated = report_engine.summary_ai(scope.child, records)
    source = "ai" if generated else "fallback"
    body_data = generated or report_engine.summary_fallback(records)
    row = ReportSummary(
        user_id=scope.user.id,
        profile_id=scope.profile_id,
        period_from=from_day.isoformat(),
        period_to=to_day.isoformat(),
        source_version=version,
        status="CURRENT",
        source=source,
        body=body_data.model_dump(by_alias=True),
    )
    db.add(row)
    db.flush()
    body = SummaryResponse(summary=report_engine.summary_out(row), reused=False)
    idempotency.remember(db, scope.user.id, idempotency_key, route, body, status_code=201)
    db.commit()
    return body


@router.get("/reports/summaries/{summary_id}", response_model=SummaryResponse)
def get_summary(
    summary_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    _check_reports_permission(scope)
    row = db.get(ReportSummary, summary_id)
    if row is None or row.user_id != scope.user.id:
        raise ApiError(404, "SUMMARY_NOT_FOUND", "요약을 찾을 수 없어요.")
    report_engine.refresh_status(db, row)
    body = SummaryResponse(summary=report_engine.summary_out(row), reused=True)
    db.commit()
    return body


# --- 보호자 월간 상담 ------------------------------------------------------------


def _consultation_out(db: Session, row: GuardianConsultation) -> ConsultationOut:
    questions = db.scalars(
        select(ConsultationQuestion)
        .where(ConsultationQuestion.consultation_id == row.id)
        .order_by(ConsultationQuestion.created_at)
    )
    return ConsultationOut(
        id=row.id,
        profile_id=row.profile_id,
        period=row.period,
        source=row.source,
        consultation=ConsultationBody(**row.body),
        questions=[report_engine.question_out(q) for q in questions],
        created_at=cursor.iso(row.created_at) or "",
    )


def _eligibility(db: Session, scope: ProfileScope, period: str) -> EligibilityResponse:
    first = report_engine.first_record_at(db, scope.user.id)
    today = report_engine.kst_date(clock.now())
    days = (clock.now() - first).days if first else 0
    need = report_engine.min_days()
    remaining = max(0, need - days)
    first_day, last_day = report_engine.month_bounds(period)
    records = report_engine.collect(db, scope.user.id, first_day, min(last_day, today))
    existing = db.scalar(
        select(GuardianConsultation).where(
            GuardianConsultation.profile_id == scope.profile_id, GuardianConsultation.period == period
        )
    )
    if first is None:
        reason = "아직 이야기 기록이 없어요."
    elif remaining:
        reason = f"이용 {need}일이 지나면 만들 수 있어요. {remaining}일 남았어요."
    elif existing is not None:
        reason = "이 달 상담은 이미 만들어 두었어요."
    elif not records.stories:
        reason = "이 달에 완성한 이야기가 없어 정리할 기록이 부족해요."
    else:
        reason = "이 달 활동으로 상담을 만들 수 있어요."
    return EligibilityResponse(
        profile_id=scope.profile_id,
        eligible=first is not None and not remaining and existing is None and bool(records.stories),
        period=period,
        reason=reason,
        days_remaining=remaining,
        completed_stories=len(records.stories),
        already_created=existing is not None,
    )


@router.get("/guardian/consultations/eligibility", response_model=EligibilityResponse)
def consultation_eligibility(
    period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM(KST). 비우면 지난달"),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    _check_reports_permission(scope)
    return _eligibility(db, scope, period or report_engine.previous_month(report_engine.kst_date(clock.now())))


@router.post("/guardian/consultations", response_model=ConsultationResponse, status_code=201)
def create_consultation(
    req: ConsultationCreate,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    _check_reports_permission(scope)
    _check_profile(scope, req.profile_id)
    route = "POST /guardian/consultations"
    if (replayed := idempotency.replay(db, scope.user.id, idempotency_key, route)) is not None:
        return replayed
    today = report_engine.kst_date(clock.now())
    period = req.period or report_engine.previous_month(today)
    check = _eligibility(db, scope, period)
    if check.already_created:
        row = db.scalar(
            select(GuardianConsultation).where(
                GuardianConsultation.profile_id == scope.profile_id, GuardianConsultation.period == period
            )
        )
        return ConsultationResponse(consultation=_consultation_out(db, row))
    if not check.eligible:
        raise ApiError(
            409,
            "CONSULTATION_NOT_ELIGIBLE",
            check.reason,
            {"daysRemaining": check.days_remaining, "completedStories": check.completed_stories, "period": period},
        )
    first_day, last_day = report_engine.month_bounds(period)
    records = report_engine.collect(db, scope.user.id, first_day, min(last_day, today))
    generated = report_engine.consultation_ai(scope.child, records, period)
    row = GuardianConsultation(
        user_id=scope.user.id,
        profile_id=scope.profile_id,
        period=period,
        source="ai" if generated else "fallback",
        body=(generated or report_engine.consultation_fallback(records, period)).model_dump(by_alias=True),
    )
    db.add(row)
    db.flush()
    body = ConsultationResponse(consultation=_consultation_out(db, row))
    idempotency.remember(db, scope.user.id, idempotency_key, route, body, status_code=201)
    db.commit()
    return body


@router.get("/guardian/consultations", response_model=ConsultationList)
def list_consultations(
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    _check_reports_permission(scope)
    size = cursor.clamp_limit(limit)
    stmt = select(GuardianConsultation).where(GuardianConsultation.profile_id == scope.profile_id)
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(
            or_(
                GuardianConsultation.created_at < at,
                and_(GuardianConsultation.created_at == at, GuardianConsultation.id < row_id),
            )
        )
    rows = list(
        db.scalars(
            stmt.order_by(GuardianConsultation.created_at.desc(), GuardianConsultation.id.desc()).limit(size + 1)
        )
    )
    page = rows[:size]
    return ConsultationList(
        items=[
            ConsultationSummary(
                id=r.id,
                profile_id=r.profile_id,
                period=r.period,
                source=r.source,
                created_at=cursor.iso(r.created_at) or "",
            )
            for r in page
        ],
        next_cursor=cursor.encode(page[-1].created_at, page[-1].id) if len(rows) > size else None,
    )


def _own_consultation(db: Session, scope: ProfileScope, consultation_id: str) -> GuardianConsultation:
    row = db.get(GuardianConsultation, consultation_id)
    if row is None or row.user_id != scope.user.id:
        raise ApiError(404, "CONSULTATION_NOT_FOUND", "상담을 찾을 수 없어요.")
    return row


@router.get("/guardian/consultations/{consultation_id}", response_model=ConsultationResponse)
def get_consultation(
    consultation_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    _check_reports_permission(scope)
    return ConsultationResponse(consultation=_consultation_out(db, _own_consultation(db, scope, consultation_id)))


@router.post("/guardian/consultations/{consultation_id}/questions", response_model=ConsultationQuestionResponse, status_code=201)  # noqa: E501
def ask_consultation_question(
    consultation_id: str,
    req: ConsultationQuestionCreate,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    _check_reports_permission(scope)
    row = _own_consultation(db, scope, consultation_id)
    question = " ".join(req.question.split())
    answer, source = report_engine.answer_question(scope.child, row, question)
    saved = ConsultationQuestion(
        consultation_id=row.id, user_id=scope.user.id, question=question, answer=answer, source=source
    )
    db.add(saved)
    db.commit()
    return ConsultationQuestionResponse(question=report_engine.question_out(saved))

