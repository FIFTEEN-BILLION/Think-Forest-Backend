"""v1 친구들의 이야기(명세 14절) — 승인된 공개본 목록·상세, 따뜻한 추천, 불편한 내용 신고."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...db import get_session
from .. import cursor, social_community
from ..deps import CurrentUser, require_user
from ..schemas_conversation import TopicCategory
from ..social_schemas import (
    CommunityDetailResponse,
    CommunityList,
    CommunityReportCreate,
    CommunityReportResponse,
    RecommendationFilter,
    RecommendationResponse,
)

router = APIRouter(prefix="/community", tags=["v1-community"])


@router.get("/stories", response_model=CommunityList)
def list_community_stories(
    category: TopicCategory | None = Query(default=None),
    recommendation: RecommendationFilter | None = Query(default=None, description="추천 이유로 거르기"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    after = cursor.decode(cursor_raw) if cursor_raw else None
    rows, more = social_community.page(db, cu, category=category, wanted=recommendation, after=after, size=size)
    body = CommunityList(
        items=social_community.decorate(db, cu, rows),
        next_cursor=cursor.encode(rows[-1].published_at, rows[-1].id) if more and rows else None,
    )
    db.commit()
    return body


@router.get("/stories/{public_story_id}", response_model=CommunityDetailResponse)
def get_community_story(
    public_story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
):
    public = social_community.visible(db, cu, public_story_id)
    body = CommunityDetailResponse(story=social_community.detail_out(db, cu, public))
    db.commit()
    return body


@router.put("/stories/{public_story_id}/recommendation", response_model=RecommendationResponse)
def add_recommendation(
    public_story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
):
    public = social_community.visible(db, cu, public_story_id)
    social_community.recommend(db, cu, public)
    db.commit()
    return RecommendationResponse(
        story_id=public.id, recommendation_count=public.recommendation_count, recommended_by_me=True
    )


@router.delete("/stories/{public_story_id}/recommendation", response_model=RecommendationResponse)
def remove_recommendation(
    public_story_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)
):
    public = social_community.visible(db, cu, public_story_id)
    social_community.unrecommend(db, cu, public)
    db.commit()
    return RecommendationResponse(
        story_id=public.id, recommendation_count=public.recommendation_count, recommended_by_me=False
    )


@router.post("/stories/{public_story_id}/reports", response_model=CommunityReportResponse, status_code=201)
def report_community_story(
    public_story_id: str,
    req: CommunityReportCreate,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    public = social_community.visible(db, cu, public_story_id)
    row = social_community.report(db, cu, public, req.reason, req.detail)
    body = CommunityReportResponse(report=social_community.report_out(row), story_status=public.status)
    db.commit()
    return body
