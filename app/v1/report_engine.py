"""나의 발자국·성장 리포트(명세 18절)와 보호자 월간 상담(명세 29절).

- 리포트는 **진단이 아니다.** 실제 대화·이야기 행에서 센 횟수만 담는다. 점수를 만들지 않는다.
- 서술형 요약은 OpenAI(`ai_gate`)로 만들고, 막히거나 실패하면 규칙 기반 문장으로 대체한다.
- AI 에는 집계와 아이가 실제로 한 말 인용만 보낸다. 같은 기간·같은 원본 버전이면 다시 만들지 않는다.
- 날짜 경계는 KST(UTC+9) 기준이다.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from ..models import Child
from ..safety import topics as sensitive
from ..services import usage
from ..talks.planner import clip
from . import ai_gate, conversation_scope, report_prompts
from .cursor import iso
from .errors import ApiError
from .models_conversation import ChildProfile, ConversationMessage, ConversationSession, StoryRecord
from .report_schemas import (
    ActivityCounts,
    CategoryCount,
    ConsultationAnswerLLM,
    ConsultationBody,
    ConsultationQuestionOut,
    MonthlyConsultationLLM,
    ObservedBehaviors,
    PeriodOut,
    ProgressResponse,
    ReportSummaryLLM,
    SummaryBody,
    SummaryOut,
    TimelinePoint,
)

from .models_social import ConsultationQuestion, GuardianConsultation, ReportSummary  # isort: skip

PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}
MAX_QUOTES = 8
QUOTE_LIMIT = 90


def kst_date(moment: datetime) -> date:
    return (moment + timedelta(hours=9)).date()


def kst_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min) - timedelta(hours=9)


def period_bounds(from_day: date, to_day: date) -> tuple[datetime, datetime]:
    return kst_midnight_utc(from_day), kst_midnight_utc(to_day + timedelta(days=1))


def parse_day(raw: str | None, field: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": [field]}) from exc


# --- 집계 ------------------------------------------------------------------------


class Records:
    """한 기간의 원본 기록 모음. 진단이 아니라 개수와 인용만 들고 있다."""

    def __init__(self, stories: list[StoryRecord], sessions: list[ConversationSession], messages: list):
        self.stories = stories
        self.sessions = sessions
        self.messages = messages

    @property
    def source_version(self) -> str:
        """원본 기록의 지문. 이야기가 늘거나 고쳐지면 값이 달라져 이전 요약이 STALE 이 된다."""
        parts = [f"{s.id}:{s.version}:{s.updated_at.isoformat()}" for s in sorted(self.stories, key=lambda s: s.id)]
        parts.append(f"messages:{len(self.messages)}")
        parts.append(f"sessions:{len(self.sessions)}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]

    @property
    def quotes(self) -> list[str]:
        """아이가 실제로 한 말(선택지 답 제외). 이미 개인정보를 가린 문장이다."""
        said = [m for m in self.messages if (m.meta or {}).get("valid") and not (m.meta or {}).get("choice")]
        return [clip(m.content, QUOTE_LIMIT) for m in said[-MAX_QUOTES:]]


def collect(db: Session, user_id: str, from_day: date, to_day: date, profile_id: str | None = None) -> Records:
    start, end = period_bounds(from_day, to_day)
    profile = db.get(ChildProfile, profile_id) if profile_id else None
    if profile:
        user_id = profile.user_id
    stories = list(
        db.scalars(
            select(StoryRecord).where(
                StoryRecord.user_id == user_id,
                StoryRecord.created_at >= start,
                StoryRecord.created_at < end,
                conversation_scope.condition(StoryRecord.session_id, profile.child_id) if profile else True,
            )
        )
    )
    sessions = list(
        db.scalars(
            select(ConversationSession).where(
                ConversationSession.user_id == user_id,
                conversation_scope.condition(ConversationSession.id, profile.child_id) if profile else True,
                ConversationSession.kind == "STORY",
                ConversationSession.created_at < end,
                ConversationSession.last_message_at >= start,
            )
        )
    )
    ids = [s.id for s in sessions]
    messages = (
        list(
            db.scalars(
                select(ConversationMessage)
                .where(
                    ConversationMessage.session_id.in_(ids),
                    ConversationMessage.role == "USER",
                    ConversationMessage.created_at >= start,
                    ConversationMessage.created_at < end,
                )
                .order_by(ConversationMessage.created_at)
            )
        )
        if ids
        else []
    )
    return Records(stories, sessions, messages)


def _dims(message) -> set[str]:
    return set((message.meta or {}).get("dims") or [])


def counts(records: Records) -> tuple[ActivityCounts, ObservedBehaviors]:
    days = {kst_date(m.created_at) for m in records.messages}
    days |= {kst_date(s.created_at) for s in records.stories}
    said = [m for m in records.messages if (m.meta or {}).get("valid") and not (m.meta or {}).get("choice")]
    activity = ActivityCounts(
        active_days=len(days),
        completed_stories=len(records.stories),
        # 기간 안에 이야기를 나눴지만 아직 끝내지 않은 대화.
        continued_stories=len([s for s in records.sessions if s.status in ("ACTIVE", "READY_TO_FINISH")]),
        # 단어 보관함은 다른 트랙이 채운다. 아직 셀 기록이 없으므로 0.
        new_words=0,
    )
    observed = ObservedBehaviors(
        full_sentence_responses=len(said),
        reason_explanations=len([m for m in records.messages if "REASON" in _dims(m)]),
        alternative_ideas=len([m for m in records.messages if "ALTERNATIVE" in _dims(m)]),
        revised_ideas=len([m for m in records.messages if "REFLECTION" in _dims(m)]),
    )
    return activity, observed


def timeline(records: Records, from_day: date, to_day: date) -> list[TimelinePoint]:
    talked: Counter = Counter()
    responses: Counter = Counter()
    finished: Counter = Counter()
    for message in records.messages:
        responses[kst_date(message.created_at)] += 1
    for session in records.sessions:
        talked[kst_date(session.last_message_at)] += 1
    for story in records.stories:
        finished[kst_date(story.created_at)] += 1
    points = []
    day = from_day
    while day <= to_day:
        if talked.get(day) or responses.get(day) or finished.get(day):
            points.append(
                TimelinePoint(
                    date=day.isoformat(),
                    conversations=talked.get(day, 0),
                    completed_stories=finished.get(day, 0),
                    responses=responses.get(day, 0),
                )
            )
        day += timedelta(days=1)
    return points


def progress(db: Session, user_id: str, profile_id: str, period: str) -> ProgressResponse:
    days = PERIOD_DAYS[period]
    to_day = kst_date(clock.now())
    from_day = to_day - timedelta(days=days - 1)
    records = collect(db, user_id, from_day, to_day, profile_id)
    activity, observed = counts(records)
    breakdown = Counter(s.category for s in records.stories)
    return ProgressResponse(
        profile_id=profile_id,
        period=PeriodOut(**{"from": from_day.isoformat(), "to": to_day.isoformat()}),
        activity=activity,
        observed_behaviors=observed,
        timeline=timeline(records, from_day, to_day),
        category_breakdown=[CategoryCount(category=c, completed_stories=n) for c, n in sorted(breakdown.items())],
    )


# --- 서술형 요약 ----------------------------------------------------------------


def _aggregate(records: Records) -> dict:
    activity, observed = counts(records)
    return {**activity.model_dump(by_alias=True), **observed.model_dump(by_alias=True)}


def _clean_lines(values: list[str], limit: int = 3) -> list[str]:
    out = []
    for raw in values[:limit]:
        line = clip((raw or "").strip(), 100)
        if line and not sensitive.detect(line):
            out.append(line)
    return out


def summary_fallback(records: Records) -> SummaryBody:
    activity, observed = counts(records)
    highlights = [f"기간 동안 {activity.active_days}일 이야기를 나눴어요."]
    if activity.completed_stories:
        highlights.append(f"이야기 {activity.completed_stories}편을 끝까지 정리했어요.")
    if observed.reason_explanations:
        highlights.append(f"까닭을 말한 답이 {observed.reason_explanations}번 있었어요.")
    return SummaryBody(
        highlights=highlights,
        suggestions=[
            "아이가 만든 이야기를 함께 읽고 마지막 질문에 대해 이야기해 보세요.",
            "짧아도 좋으니 같은 시간에 이야기하는 습관을 만들어 보세요.",
        ],
        conversation_tips=[
            "오늘 티키와 무슨 이야기를 했어? 가장 재미있던 생각을 들려줄래?",
            "처음 생각이 바뀐 적이 있었어? 왜 바뀌었는지 궁금해.",
        ],
        evidence_story_ids=[s.id for s in records.stories],
    )


def summary_ai(child: Child, records: Records) -> SummaryBody | None:
    if not ai_gate.allowed(child) or not usage.try_consume(child.id):
        return None
    try:
        out = ai_gate.call(
            purpose="report.summary",
            instructions=report_prompts.summary_instructions(),
            user_input=report_prompts.summary_input(
                _aggregate(records), records.quotes, [s.title for s in records.stories]
            ),
            schema=ReportSummaryLLM,
        )
    except ai_gate.LlmError:
        return None
    body = SummaryBody(
        highlights=_clean_lines(out.highlights),
        suggestions=_clean_lines(out.suggestions),
        conversation_tips=_clean_lines(out.conversation_tips),
        evidence_story_ids=[s.id for s in records.stories],
    )
    if not (body.highlights and body.suggestions and body.conversation_tips):
        return None
    return body


def summary_out(row: ReportSummary) -> SummaryOut:
    return SummaryOut(
        id=row.id,
        profile_id=row.profile_id,
        period=PeriodOut(**{"from": row.period_from, "to": row.period_to}),
        status=row.status,
        source=row.source,
        summary=SummaryBody(**row.body),
        created_at=iso(row.created_at) or "",
        updated_at=iso(row.updated_at) or "",
    )


def refresh_status(db: Session, row: ReportSummary) -> ReportSummary:
    """원본 기록이 달라졌으면 STALE 로 표시한다(내용은 그대로 둔다)."""
    if row.status == "STALE":
        return row
    records = collect(
        db, row.user_id, date.fromisoformat(row.period_from), date.fromisoformat(row.period_to), row.profile_id
    )
    if records.source_version != row.source_version:
        row.status = "STALE"
    return row


# --- 보호자 월간 상담 ------------------------------------------------------------


def month_bounds(period: str) -> tuple[date, date]:
    year, month = int(period[:4]), int(period[5:7])
    if not 1 <= month <= 12:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["period"]})
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return first, last


def previous_month(today: date) -> str:
    first = today.replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def first_record_at(db: Session, user_id: str) -> datetime | None:
    return db.scalar(
        select(ConversationSession.created_at)
        .where(ConversationSession.user_id == user_id)
        .order_by(ConversationSession.created_at)
        .limit(1)
    )


def consultation_fallback(records: Records, period: str) -> ConsultationBody:
    activity, observed = counts(records)
    behaviors = [f"{period} 동안 {activity.active_days}일 이야기를 나눴어요."]
    if observed.full_sentence_responses:
        behaviors.append(f"문장으로 답한 적이 {observed.full_sentence_responses}번 있었어요.")
    if observed.alternative_ideas:
        behaviors.append(f"다른 가능성을 말한 적이 {observed.alternative_ideas}번 있었어요.")
    return ConsultationBody(
        observed_behaviors=behaviors,
        examples=[f"아이 말: “{q}”" for q in records.quotes[:3]] or ["이번 달에는 인용할 만한 문장이 적었어요."],
        questions_to_try=[
            "이 이야기에서 가장 마음에 남은 장면이 뭐야?",
            "그때 왜 그렇게 생각했는지 더 들려줄래?",
            "다음에는 어떤 이야기를 해 보고 싶어?",
        ],
        evidence_story_ids=[s.id for s in records.stories],
    )


def consultation_ai(child: Child, records: Records, period: str) -> ConsultationBody | None:
    if not ai_gate.allowed(child) or not usage.try_consume(child.id):
        return None
    try:
        out = ai_gate.call(
            purpose="guardian.consultation.v1",
            instructions=report_prompts.consultation_instructions(),
            user_input=report_prompts.consultation_input(
                period, _aggregate(records), records.quotes, [s.title for s in records.stories]
            ),
            schema=MonthlyConsultationLLM,
        )
    except ai_gate.LlmError:
        return None
    body = ConsultationBody(
        observed_behaviors=_clean_lines(out.observed_behaviors),
        examples=_clean_lines(out.examples),
        questions_to_try=_clean_lines(out.questions_to_try),
        evidence_story_ids=[s.id for s in records.stories],
    )
    if not (body.observed_behaviors and body.examples and body.questions_to_try):
        return None
    return body


def answer_question(child: Child, consultation: GuardianConsultation, question: str) -> tuple[str, str]:
    """(답, 출처). AI 가 막히거나 실패하면 정해진 안내 문장으로 답한다."""
    fallback = (
        "이 상담은 대화에서 관찰된 기록만 정리한 것이라 진단이나 판단을 드리지 못해요. "
        "적어 주신 질문은 아이와 직접 이야기해 보시고, 걱정되는 점은 전문 상담 기관과 나눠 주세요."
    )
    if not ai_gate.allowed(child) or not usage.try_consume(child.id):
        return fallback, "fallback"
    try:
        out = ai_gate.call(
            purpose="guardian.consultation.question",
            instructions=report_prompts.answer_instructions(),
            user_input=report_prompts.answer_input(consultation.body, question),
            schema=ConsultationAnswerLLM,
        )
    except ai_gate.LlmError:
        return fallback, "fallback"
    answer = clip((out.answer or "").strip(), 300)
    if not answer or sensitive.detect(answer):
        return fallback, "fallback"
    return answer, "ai"


def question_out(row: ConsultationQuestion) -> ConsultationQuestionOut:
    return ConsultationQuestionOut(
        id=row.id,
        question=row.question,
        answer=row.answer,
        source=row.source,
        created_at=iso(row.created_at) or "",
    )


def min_days() -> int:
    return get_settings().consultation_min_days
