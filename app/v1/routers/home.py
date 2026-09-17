"""v1 홈 조합 API 와 /me."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import topic_catalog
from ..cursor import iso
from ..deps import CurrentUser, require_user
from ..models_conversation import ConversationMessage, ConversationSession, StoryRecord
from ..profile_status import completed_profile
from ..schemas_conversation import (
    HomeProfile,
    HomeResponse,
    MeProfile,
    MeResponse,
    MeUser,
    Recommendation,
    ResumeOut,
    WeeklyActivity,
)

router = APIRouter(tags=["v1-home"])


def _week_start_utc() -> datetime:
    """오늘 포함 최근 7일(KST)의 시작 시각을 UTC naive 로."""
    today = clock.kst(clock.now()).date()
    start_kst = datetime.combine(today - timedelta(days=6), time.min, tzinfo=clock.KST)
    return start_kst.astimezone(clock.KST).replace(tzinfo=None) - timedelta(hours=9)


@router.get("/me", response_model=MeResponse)
def me(cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    profile = completed_profile(db, cu.user)
    return MeResponse(
        user=MeUser(id=cu.id, role=cu.user.role, needs_first_greeting=profile is None),
        profile=MeProfile(
            id=profile.id,
            nickname=profile.nickname,
            grade_or_age_band=profile.grade_or_age_band,
            interests=list(profile.interests),
            growth_goal=profile.growth_goal,
        )
        if profile
        else None,
    )


@router.get("/home", response_model=HomeResponse)
def home(cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    profile = completed_profile(db, cu.user)
    minutes = topic_catalog.estimated_minutes()
    recommendations = [
        Recommendation(
            topic_id=snap["apiId"],
            title=snap["title"],
            category=snap["apiCategory"],
            reason=reason,
            estimated_minutes=minutes,
        )
        for snap, reason in topic_catalog.recommend(db, cu.id, profile)
    ]
    latest = db.scalar(
        select(ConversationSession)
        .where(
            ConversationSession.user_id == cu.id,
            ConversationSession.kind == "STORY",
            ConversationSession.status.in_(("ACTIVE", "READY_TO_FINISH")),
        )
        .order_by(ConversationSession.updated_at.desc(), ConversationSession.id.desc())
    )
    since = _week_start_utc()
    stamps = db.scalars(
        select(ConversationMessage.created_at)
        .join(ConversationSession, ConversationSession.id == ConversationMessage.session_id)
        .where(
            ConversationSession.user_id == cu.id,
            ConversationMessage.role == "USER",
            ConversationMessage.created_at >= since,
        )
    )
    days = {clock.kst(stamp).date() for stamp in stamps}
    completed = db.scalar(
        select(func.count(StoryRecord.id)).where(StoryRecord.user_id == cu.id, StoryRecord.created_at >= since)
    )
    return HomeResponse(
        profile=HomeProfile(
            nickname=(profile.nickname if profile else None) or cu.child.nickname or None,
            needs_first_greeting=profile is None,
        ),
        recommendations=recommendations,
        resume=ResumeOut(
            conversation_id=latest.id,
            title=(latest.topic or {}).get("title", ""),
            status=latest.status,
            updated_at=iso(latest.updated_at) or "",
        )
        if latest
        else None,
        recent_words=[],
        community_stories=[],
        weekly_activity=WeeklyActivity(conversation_days=len(days), completed_stories=completed or 0),
    )
