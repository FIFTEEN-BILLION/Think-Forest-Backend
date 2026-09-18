"""v1 홈 조합 API 와 /me."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import activity_home, activity_schedules, conversation_scope, models_accounts, topic_catalog
from ..cursor import iso
from ..deps import CurrentUser, require_user
from ..models_conversation import ChildProfile, ConversationMessage, ConversationSession, StoryRecord
from ..profile_status import completed_profile
from ..schemas_accounts import AccountMeResponse, MeProfileItem
from ..schemas_conversation import (
    HomeProfile,
    HomeResponse,
    MeProfile,
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


@router.get("/me", response_model=AccountMeResponse)
def me(cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    """로그인 계정과 기본 프로필, 그리고 이 계정이 볼 수 있는 아이 프로필 전부.

    `profile` 은 기본 프로필(첫인사를 마친 경우), `profiles` 는 내가 만든 프로필 + 초대로 연결된 프로필이다.
    """
    profile = completed_profile(db, cu.user)
    members = models_accounts.active_members(db, cu.user)
    items = []
    for member in members:
        row = db.get(ChildProfile, member.profile_id)
        if row is None:
            continue
        items.append(
            MeProfileItem(
                id=row.id,
                nickname=row.nickname,
                grade_or_age_band=row.grade_or_age_band,
                interests=list(row.interests or []),
                growth_goal=row.growth_goal,
                role=member.role,
                permissions=list(member.permissions or []),
                is_default=member.is_default,
                needs_first_greeting=row.completed_at is None,
            )
        )
    db.commit()  # 기존 계정 이어받기로 만든 소속 행을 남긴다.
    return AccountMeResponse(
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
        profiles=items,
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
        # 운영자 편성(`/admin/topic-schedules`)이 있으면 그 주제를 앞으로 올린다. 이유 문장은 편성에 적힌 것을 쓴다.
        for snap, reason in activity_schedules.reorder(db, topic_catalog.recommend(db, cu.id, profile))
    ]
    latest = db.scalar(
        select(ConversationSession)
        .where(
            ConversationSession.user_id == cu.id,
            conversation_scope.condition(ConversationSession.id, cu.child.id),
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
            conversation_scope.condition(ConversationSession.id, cu.child.id),
            ConversationMessage.role == "USER",
            ConversationMessage.created_at >= since,
        )
    )
    days = {clock.kst(stamp).date() for stamp in stamps}
    completed = db.scalar(
        select(func.count(StoryRecord.id)).where(
            StoryRecord.user_id == cu.id,
            StoryRecord.created_at >= since,
            conversation_scope.condition(StoryRecord.session_id, cu.child.id),
        )
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
        # 단어 보관함·친구 이야기 테이블은 다른 트랙이 만든다. 아직 없으면 빈 목록으로 내려간다.
        recent_words=activity_home.recent_words(db, cu.id, profile_id=profile.id if profile else None),
        community_stories=activity_home.community_stories(db, cu),
        weekly_activity=WeeklyActivity(conversation_days=len(days), completed_stories=completed or 0),
    )


@router.get("/service-info")
def service_info(cu: CurrentUser = Depends(require_user)):
    from ...config import get_settings

    settings = get_settings()
    return {
        "service": "생각친구 티키",
        "storage": "서버 데이터베이스",
        "aiAvailable": settings.openai_enabled,
        "speechAvailable": settings.speech_enabled and bool(settings.openai_api_key),
        "streamingAvailable": settings.speech_enabled and settings.websocket_enabled and bool(settings.openai_api_key),
    }
