"""친구들의 이야기(명세 14절) — 승인된 공개본만, 추천은 한 사람당 한 번, 신고가 쌓이면 자동 숨김.

응답에는 작성자의 실제 이름·학교·정확한 나이가 없다. 공개본에 저장된 별명과 넓은 나이대만 쓴다.
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from .cursor import iso
from .deps import CurrentUser
from .errors import ApiError
from .models_accounts import ProfileMember
from .models_conversation import ChildProfile, ConversationOwner, StoryRecord
from .models_social import CommunityReport, PublicStory, ShareRequest, StoryRecommendation
from .social_schemas import CommunityReportOut, PublicStoryDetail
from .social_sharing import age_band, public_out, sync_edits

# 이 사유로 신고가 들어오면 수를 채우지 않아도 바로 숨긴다.
HIGH_RISK_REASONS = ("PERSONAL_INFO",)

REASONS = {
    "SIMILAR_AGE": "비슷한 나이의 친구가 만든 이야기",
    "SAME_CATEGORY": "네가 자주 이야기한 분야의 이야기",
    "POPULAR": "친구들이 따뜻한 추천을 많이 남긴 이야기",
    "NEW": "방금 올라온 새 이야기",
}


def _my_profile(db: Session, cu: CurrentUser) -> ChildProfile | None:
    return db.scalar(select(ChildProfile).where(ChildProfile.child_id == cu.child.id))


def _my_categories(db: Session, cu: CurrentUser) -> set[str]:
    return set(db.scalars(select(StoryRecord.category).where(StoryRecord.user_id == cu.id).distinct()))


def reason_for(public: PublicStory, my_band: str, my_categories: set[str], top_count: int) -> str:
    """추천 이유는 반드시 한 문장으로 준다(왜 보이는지 아이가 알 수 있게)."""
    if my_band != "또래 친구" and public.age_band == my_band:
        return REASONS["SIMILAR_AGE"]
    if public.category in my_categories:
        return REASONS["SAME_CATEGORY"]
    if public.recommendation_count and public.recommendation_count >= top_count:
        return REASONS["POPULAR"]
    return REASONS["NEW"]


def matches_filter(public: PublicStory, wanted: str, my_band: str, my_categories: set[str], top_count: int) -> bool:
    return reason_for(public, my_band, my_categories, top_count) == REASONS[wanted]


def audience_scope(db: Session, cu: CurrentUser):
    # JSON.contains()는 PostgreSQL에서도 문자열 LIKE로 변환되어 조회가 실패한다.
    # 현재 계정의 소속만 읽고, JSON 배열의 정확한 권한 값을 검사한다.
    members = db.scalars(
        select(ProfileMember).where(ProfileMember.user_id == cu.id, ProfileMember.revoked_at.is_(None))
    )
    readable_profiles = [
        member.profile_id
        for member in members
        if member.role == "OWNER" or "VIEW_STORIES" in (member.permissions or [])
    ]
    shared = (
        select(StoryRecord.id)
        .join(ConversationOwner, ConversationOwner.session_id == StoryRecord.session_id)
        .join(ChildProfile, ChildProfile.child_id == ConversationOwner.child_id)
        .where(ChildProfile.id.in_(readable_profiles))
    )
    return or_(PublicStory.audience == "PEERS", PublicStory.author_user_id == cu.id, PublicStory.story_id.in_(shared))


def visible(db: Session, cu: CurrentUser, public_story_id: str) -> PublicStory:
    """공개된 것만 보인다. 없는 id·숨긴 이야기는 똑같이 404(존재 여부를 알려 주지 않는다)."""
    public = db.scalar(select(PublicStory).where(PublicStory.id == public_story_id, audience_scope(db, cu)))
    if public is not None:
        request = db.get(ShareRequest, public.share_request_id)
        if request is not None:
            sync_edits(db, request, public)
    if public is None or public.status != "PUBLISHED":
        raise ApiError(404, "PUBLIC_STORY_NOT_FOUND", "이야기를 찾을 수 없어요.")
    return public


def recommended_ids(db: Session, cu: CurrentUser, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = db.scalars(
        select(StoryRecommendation.public_story_id).where(
            StoryRecommendation.user_id == cu.id, StoryRecommendation.public_story_id.in_(ids)
        )
    )
    return set(rows)


def page(
    db: Session, cu: CurrentUser, *, category: str | None, wanted: str | None, after: tuple | None, size: int
) -> tuple[list, bool]:
    stmt = select(PublicStory).where(PublicStory.status == "PUBLISHED", audience_scope(db, cu))
    if category:
        stmt = stmt.where(PublicStory.category == category)
    if after:
        at, row_id = after
        stmt = stmt.where(
            or_(PublicStory.published_at < at, and_(PublicStory.published_at == at, PublicStory.id < row_id))
        )
    # 필터는 보는 사람마다 달라 SQL 로 거르지 못한다. 넉넉히 읽고 파이썬에서 거른 뒤 자른다.
    rows = list(db.scalars(stmt.order_by(PublicStory.published_at.desc(), PublicStory.id.desc()).limit(size * 5 + 1)))
    # 승인 뒤 본문이 바뀐 공개본은 여기서 멈춘다.
    for public in rows:
        request = db.get(ShareRequest, public.share_request_id)
        if request is not None:
            sync_edits(db, request, public)
    rows = [p for p in rows if p.status == "PUBLISHED"]
    if wanted:
        my_band = age_band(_my_profile(db, cu))
        categories = _my_categories(db, cu)
        top = _top_count(db)
        rows = [p for p in rows if matches_filter(p, wanted, my_band, categories, top)]
    return rows[:size], len(rows) > size


def _top_count(db: Session) -> int:
    """'인기' 기준 — 추천이 1개 이상이면서 상위권으로 볼 최소 수."""
    top = select(PublicStory.recommendation_count).order_by(PublicStory.recommendation_count.desc()).limit(1)
    best = db.scalar(top)
    return max(1, (best or 0))


def decorate(db: Session, cu: CurrentUser, rows: list[PublicStory]) -> list:
    mine = recommended_ids(db, cu, [p.id for p in rows])
    my_band = age_band(_my_profile(db, cu))
    categories = _my_categories(db, cu)
    top = _top_count(db)
    return [public_out(p, recommended_by_me=p.id in mine, reason=reason_for(p, my_band, categories, top)) for p in rows]


def detail_out(db: Session, cu: CurrentUser, public: PublicStory) -> PublicStoryDetail:
    base = decorate(db, cu, [public])[0]
    return PublicStoryDetail(
        **base.model_dump(),
        body=public.body,
        thought_journey=public.thought_journey or {},
        mine=public.author_user_id == cu.id,
    )


# --- 추천 ------------------------------------------------------------------------


def recommend(db: Session, cu: CurrentUser, public: PublicStory) -> None:
    existing = db.scalar(
        select(StoryRecommendation).where(
            StoryRecommendation.public_story_id == public.id, StoryRecommendation.user_id == cu.id
        )
    )
    if existing is not None:  # 한 사람은 한 번만. 다시 눌러도 수가 올라가지 않는다.
        return
    db.add(StoryRecommendation(public_story_id=public.id, user_id=cu.id))
    public.recommendation_count += 1


def unrecommend(db: Session, cu: CurrentUser, public: PublicStory) -> None:
    existing = db.scalar(
        select(StoryRecommendation).where(
            StoryRecommendation.public_story_id == public.id, StoryRecommendation.user_id == cu.id
        )
    )
    if existing is None:
        return
    db.delete(existing)
    public.recommendation_count = max(0, public.recommendation_count - 1)


# --- 신고 ------------------------------------------------------------------------


def report(db: Session, cu: CurrentUser, public: PublicStory, reason: str, detail: str | None) -> CommunityReport:
    existing = db.scalar(
        select(CommunityReport).where(
            CommunityReport.public_story_id == public.id, CommunityReport.reporter_user_id == cu.id
        )
    )
    if existing is not None:
        return existing
    row = CommunityReport(public_story_id=public.id, reporter_user_id=cu.id, reason=reason, detail=detail)
    db.add(row)
    public.report_count += 1
    threshold = get_settings().community_report_hide_threshold
    if reason in HIGH_RISK_REASONS or public.report_count >= threshold:
        # 자동 숨김. 신고는 OPEN 으로 남겨 운영자 검토 대기열에 그대로 둔다.
        public.status = "HIDDEN"
        public.hidden_at = clock.now()
        request = db.get(ShareRequest, public.share_request_id)
        if request is not None:
            request.status = "HIDDEN"
    db.flush()
    return row


def report_out(row: CommunityReport) -> CommunityReportOut:
    return CommunityReportOut(
        id=row.id,
        public_story_id=row.public_story_id,
        reason=row.reason,
        detail=row.detail,
        status=row.status,
        resolution=row.resolution,
        created_at=iso(row.created_at) or "",
    )
