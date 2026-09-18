"""홈 미리보기는 단어장·커뮤니티 화면과 같은 저장 데이터와 공개 조건을 사용한다."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import social_community
from .deps import CurrentUser
from .models_library import WordbookEntry
from .schemas_conversation import CommunityStoryPreview, RecentWord


def recent_words(db: Session, user_id: str, limit: int = 3, *, profile_id: str | None = None) -> list[RecentWord]:
    if profile_id is None:
        return []
    rows = db.scalars(
        select(WordbookEntry)
        .where(WordbookEntry.profile_id == profile_id)
        .order_by(WordbookEntry.created_at.desc())
        .limit(limit)
    )
    return [RecentWord(word=row.word, meaning=row.meaning) for row in rows]


def community_stories(db: Session, cu: CurrentUser, limit: int = 3) -> list[CommunityStoryPreview]:
    rows, _ = social_community.page(db, cu, category=None, wanted=None, after=None, size=limit)
    return [CommunityStoryPreview(id=row.id, title=row.title) for row in rows]
