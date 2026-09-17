"""v1 주제 — 은행 주제와 내가 만든 주제 목록·상세, 직접 입력한 주제의 안전 검사와 저장."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from ...models import SafetyEvent
from ...safety import pii
from ...safety import topics as sensitive
from ...talks.planner import clip
from .. import ai_gate, cursor, idempotency, topic_catalog
from ..deps import CurrentUser, require_user
from ..errors import ApiError
from ..models_conversation import UserTopic
from ..profile_status import completed_profile
from ..schemas_conversation import (
    CreatedTopic,
    TopicCategory,
    TopicCreateRequest,
    TopicCreateResponse,
    TopicDetail,
    TopicDetailResponse,
    TopicItem,
    TopicList,
    TopicSafety,
)

router = APIRouter(prefix="/topics", tags=["v1-topics"])
UNSAFE_TOPIC_MESSAGE = "그 주제로는 이야기하기 어려워요. 다른 주제를 골라 볼까요?"


def _item(snapshot: dict) -> TopicItem:
    return TopicItem(
        id=snapshot["apiId"],
        title=snapshot["title"],
        category=snapshot["apiCategory"],
        source="USER" if snapshot.get("source") == "user" else "BANK",
        hook=snapshot.get("hook", ""),
        estimated_minutes=topic_catalog.estimated_minutes(),
    )


@router.get("", response_model=TopicList)
def list_topics(
    category: TopicCategory | None = Query(default=None),
    recommended: bool | None = Query(default=None, description="true 면 오늘의 추천 주제만"),
    query: str | None = Query(default=None, max_length=40),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    offset = cursor.decode_offset(cursor_raw)
    topics = topic_catalog.all_topics(db, cu.id)
    if recommended:
        picked = {snap["apiId"] for snap, _ in topic_catalog.recommend(db, cu.id, completed_profile(db, cu.user))}
        topics = [t for t in topics if t["apiId"] in picked]
    if category:
        topics = [t for t in topics if t["apiCategory"] == category]
    if query and query.strip():
        q = query.strip()
        topics = [t for t in topics if q in t["title"] or q in t.get("hook", "")]
    page = topics[offset : offset + size]
    next_cursor = cursor.encode_offset(offset + size) if len(topics) > offset + size else None
    return TopicList(items=[_item(t) for t in page], next_cursor=next_cursor)


@router.get("/{topic_id}", response_model=TopicDetailResponse)
def get_topic(topic_id: str, cu: CurrentUser = Depends(require_user), db: Session = Depends(get_session)):
    snapshot = topic_catalog.resolve(db, cu.id, topic_id)
    if snapshot is None:
        raise ApiError(404, "TOPIC_NOT_FOUND", "주제를 찾을 수 없어요.")
    return TopicDetailResponse(
        topic=TopicDetail(**_item(snapshot).model_dump(), questions=topic_catalog.questions(snapshot))
    )


@router.post("", response_model=TopicCreateResponse, status_code=201)
def create_topic(
    req: TopicCreateRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    route = "POST /topics"
    if (replayed := idempotency.replay(db, cu.id, idempotency_key, route)) is not None:
        return replayed
    title = " ".join(req.title.split())
    flag = sensitive.detect(title)
    moderation = None if flag else ai_gate.moderate(cu.child, title)
    if flag or (moderation is not None and moderation.flagged):
        category = flag.category if flag else "moderation"
        escalate = flag.escalate if flag else any(c.startswith("self-harm") for c in moderation.categories)
        db.add(SafetyEvent(child_id=cu.child.id, talk_id=None, category=category, escalate=escalate))
        db.commit()
        raise ApiError(422, "UNSAFE_TOPIC", UNSAFE_TOPIC_MESSAGE)
    masked = clip(pii.mask(title, names=False).text, 40)
    topic = UserTopic(user_id=cu.id, title=masked, category=req.category, created_at=clock.now())
    db.add(topic)
    db.flush()
    out = TopicCreateResponse(
        topic=CreatedTopic(id=topic.id, title=topic.title, category=topic.category, source="USER"),
        safety=TopicSafety(allowed=True, reason=None),
    )
    idempotency.remember(db, cu.id, idempotency_key, route, out, status_code=201)
    db.commit()
    return out
