"""v1 책장 공통 조각 — 소유권 확인, 아이 글 안전 검사, 낙관적 잠금(If-Match/version).

이야기·단어장·이야기책이 같은 규칙을 쓰도록 여기에 모은다.
다른 사람 것은 언제나 404 로 답한다(있다는 사실도 알리지 않는다).
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..safety import pii
from ..safety import topics as sensitive
from ..talks.planner import clip
from . import chat
from .cursor import iso
from .deps import CurrentUser, ProfileScope
from .errors import ApiError
from .models_conversation import StoryRecord
from .schemas_conversation import StorySummary

_ETAG = re.compile(r'(?:W/)?"?(\d+)"?')


def current_user(scope: ProfileScope) -> CurrentUser:
    """안전 검사·AI 관문이 쓰는 형태로 바꾼다(추가 조회 없음)."""
    return CurrentUser(user=scope.user, child=scope.child)


def own_story(db: Session, user_id: str, story_id: str) -> StoryRecord:
    story = db.get(StoryRecord, story_id)
    if story is None or story.user_id != user_id:
        raise ApiError(404, "STORY_NOT_FOUND", "이야기를 찾을 수 없어요.")
    return story


def story_summary(story: StoryRecord) -> StorySummary:
    return StorySummary(
        id=story.id,
        title=story.title,
        summary=story.summary,
        category=story.category,
        favorite=story.favorite,
        version=story.version,
        source_conversation_id=story.session_id,
        created_at=iso(story.created_at) or "",
        updated_at=iso(story.updated_at) or "",
    )


def child_text(db: Session, cu: CurrentUser, text: str, limit: int) -> str:
    """아이가 직접 쓴 글: 민감 주제면 422(저장 안 함), 아니면 개인정보를 가려 돌려준다."""
    cleaned = " ".join((text or "").split())
    masked, _ = chat.screen(db, cu, cleaned, allow_personal_info=False, names=False)
    return clip(masked, limit)


def safe_ai_line(text: str, limit: int) -> str | None:
    """AI 가 만든 문장: 민감 표현이 있으면 버린다(호출한 쪽이 규칙 문장으로 대신한다)."""
    return chat.safe_line(pii.mask(text or "", names=False).text, limit)


def unsafe_free_text(text: str) -> bool:
    return sensitive.detect(text) is not None


def check_version(current: int, if_match: str | None, body_version: int | None) -> None:
    """If-Match 헤더나 본문 version 중 하나는 반드시 있어야 한다. 값이 다르면 409."""
    asked = body_version
    if if_match:
        found = _ETAG.fullmatch(if_match.strip())
        if found is None:
            raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["If-Match"]})
        asked = int(found.group(1))
    if asked is None:
        raise ApiError(400, "INVALID_INPUT", "지금 보고 있는 version 을 함께 보내 주세요.", {"fields": ["version"]})
    if asked != current:
        raise ApiError(
            409,
            "VERSION_CONFLICT",
            "다른 곳에서 먼저 고쳤어요. 새로 불러온 뒤 다시 저장해 주세요.",
            {"currentVersion": current},
        )
