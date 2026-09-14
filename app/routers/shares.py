"""모험 이야기 공유 — 친구·또래·보호자가 만든 이야기를 함께 본다.

- 아이의 이야기는 '공유 요청' 권한이 있어야 요청할 수 있고, 보호자가 승인해야 게시된다.
- 원본이 아니라 개인정보를 가린 스냅숏을 게시한다. 작성자는 별명만 보인다.
- 가족·모임 범위는 승인 즉시 게시. 전체 공개는 Moderation 통과가 필요하고,
  검사할 수 없으면(키 없음·ZDR 전 아동 콘텐츠) 사람 검토 대기로 둔다.
- 신고가 3건 쌓이면 자동으로 숨긴다.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import Viewer, ai_block_reason, require_child, require_guardian, require_permission, require_viewer
from ..db import get_session
from ..models import Book, Child, Family, SharedItem, ShareReport, Story
from ..safety import pii
from ..safety import topics as sensitive
from ..schemas.library import AdventureIn, DecisionIn, ShareOut, ShareRequest
from ..services import moderation, sharing
from ..talks import topics as bank

router = APIRouter(tags=["shares"])

REPORT_HIDE_THRESHOLD = 3


def share_out(item: SharedItem) -> ShareOut:
    return ShareOut(
        id=item.id,
        kind=item.kind,
        title=item.title,
        author_label=item.author_label,
        visibility=item.visibility,
        status=item.status,
        body=item.body,
        created_at=item.created_at,
        published_at=item.published_at,
    )


def _mask(text: str) -> str:
    return pii.mask(text or "").text


def _story_body(story: Story) -> dict:
    return {
        "scenes": [
            {"heading": _mask(s["heading"]), "text": _mask(s["text"]), "visual": s.get("visual", "star")}
            for s in story.scenes
        ],
        "endingQuestion": _mask(story.ending_question),
    }


def _texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in _texts(v)]
    return []


def _check_circle(db: Session, family_id: str, visibility: str, circle_id: str | None) -> str | None:
    if visibility != "circle":
        return None
    if not circle_id or circle_id not in sharing.circle_ids(db, family_id):
        raise HTTPException(status_code=422, detail="invalid_circle")
    return circle_id


def _publish(item: SharedItem, author_child: Child | None) -> None:
    if item.visibility in ("family", "circle"):
        item.status, item.published_at = "published", clock.now()
        return
    if author_child is not None and ai_block_reason(author_child):
        item.status, item.moderation = "pending_review", {"available": False, "reason": "child_content_not_sent"}
        return
    result = moderation.moderate("\n".join([item.title, *_texts(item.body)]))
    if not result.available:
        item.status, item.moderation = "pending_review", {"available": False}
    elif result.flagged:
        item.status = "rejected"
        item.moderation = {"available": True, "flagged": True, "categories": list(result.categories)}
    else:
        item.status, item.published_at = "published", clock.now()
        item.moderation = {"available": True, "flagged": False}


def _family_item(db: Session, family: Family, item_id: str) -> SharedItem:
    item = db.get(SharedItem, item_id)
    if item is None or item.family_id != family.id:
        raise HTTPException(status_code=404, detail="share_not_found")
    return item


@router.post("/children/me/shares", response_model=ShareOut, status_code=201)
def request_share(
    req: ShareRequest, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> ShareOut:
    require_permission(child, "publish_request")
    circle_id = _check_circle(db, child.family_id, req.visibility, req.circle_id)
    if req.kind == "story":
        story = db.get(Story, req.ref_id)
        if story is None or story.child_id != child.id:
            raise HTTPException(status_code=404, detail="story_not_found")
        title, body = _mask(story.title), _story_body(story)
    else:
        book = db.get(Book, req.ref_id)
        if book is None or book.child_id != child.id:
            raise HTTPException(status_code=404, detail="book_not_found")
        stories = [s for s in (db.get(Story, sid) for sid in book.story_ids) if s]
        title = _mask(book.title)
        body = {"stories": [{"title": _mask(s.title), **_story_body(s)} for s in stories]}
    if any(sensitive.detect(t) for t in [title, *_texts(body)]):
        raise HTTPException(status_code=422, detail="unsafe_content")
    item = SharedItem(
        family_id=child.family_id,
        child_id=child.id,
        author_label=child.nickname or "친구",
        kind=req.kind,
        ref_id=req.ref_id,
        title=title,
        body=body,
        visibility=req.visibility,
        circle_id=circle_id,
        status="pending_guardian",
    )
    db.add(item)
    db.commit()
    return share_out(item)


@router.get("/guardian/shares", response_model=list[ShareOut])
def guardian_shares(
    status: str | None = None, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> list[ShareOut]:
    query = select(SharedItem).where(SharedItem.family_id == family.id)
    if status:
        query = query.where(SharedItem.status == status)
    return [share_out(i) for i in db.scalars(query.order_by(SharedItem.created_at.desc()))]


@router.post("/guardian/shares/{item_id}/decision", response_model=ShareOut)
def decide_share(
    item_id: str, req: DecisionIn, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> ShareOut:
    item = _family_item(db, family, item_id)
    if item.status != "pending_guardian":
        raise HTTPException(status_code=409, detail="not_pending_guardian")
    if req.approve:
        _publish(item, db.get(Child, item.child_id) if item.child_id else None)
    else:
        item.status = "rejected"
    db.commit()
    return share_out(item)


@router.post("/guardian/adventures", response_model=ShareOut, status_code=201)
def create_adventure(
    req: AdventureIn, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> ShareOut:
    """보호자가 만든 모험 이야기(주제·첫 질문·이어 갈 질문). 아이들이 이 모험으로 대화를 시작한다."""
    circle_id = _check_circle(db, family.id, req.visibility, req.circle_id)
    texts = [req.title, req.hook, *req.follow_ups]
    if any(sensitive.detect(t) for t in texts):
        raise HTTPException(status_code=422, detail="unsafe_content")
    category = req.category if req.category in bank.CATEGORIES else "custom"
    item = SharedItem(
        family_id=family.id,
        author_label="보호자",
        kind="adventure",
        title=_mask(req.title),
        body={
            "category": category,
            "hook": _mask(req.hook),
            "followUps": [_mask(f) for f in req.follow_ups],
            "visual": bank.CATEGORIES[category]["visual"],
        },
        visibility=req.visibility,
        circle_id=circle_id,
        status="draft",
    )
    _publish(item, None)
    db.add(item)
    db.commit()
    return share_out(item)


@router.delete("/guardian/shares/{item_id}", status_code=204)
def hide_share(
    item_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> Response:
    _family_item(db, family, item_id).status = "hidden"
    db.commit()
    return Response(status_code=204)


def _visible(db: Session, viewer: Viewer, item_id: str) -> SharedItem:
    if viewer.child is not None:
        require_permission(viewer.child, "browse_shared")
    item = db.get(SharedItem, item_id)
    if item is None or not sharing.is_visible(item, viewer.family_id, sharing.circle_ids(db, viewer.family_id)):
        raise HTTPException(status_code=404, detail="share_not_found")
    return item


@router.get("/shares", response_model=list[ShareOut])
def browse_shares(
    kind: Literal["story", "book", "adventure"] | None = None,
    scope: Literal["family", "circle", "community"] | None = None,
    viewer: Viewer = Depends(require_viewer),
    db: Session = Depends(get_session),
) -> list[ShareOut]:
    if viewer.child is not None:
        require_permission(viewer.child, "browse_shared")
    circles = sharing.circle_ids(db, viewer.family_id)
    items = db.scalars(
        select(SharedItem).where(SharedItem.status == "published").order_by(SharedItem.published_at.desc()).limit(200)
    )
    visible = [
        i
        for i in items
        if sharing.is_visible(i, viewer.family_id, circles)
        and (kind is None or i.kind == kind)
        and (scope is None or i.visibility == scope)
    ]
    return [share_out(i) for i in visible[:50]]


@router.get("/shares/{item_id}", response_model=ShareOut)
def get_share(item_id: str, viewer: Viewer = Depends(require_viewer), db: Session = Depends(get_session)) -> ShareOut:
    return share_out(_visible(db, viewer, item_id))


@router.post("/shares/{item_id}/reports", response_model=ShareOut)
def report_share(
    item_id: str, viewer: Viewer = Depends(require_viewer), db: Session = Depends(get_session)
) -> ShareOut:
    item = _visible(db, viewer, item_id)
    exists = db.scalar(
        select(ShareReport).where(ShareReport.item_id == item.id, ShareReport.family_id == viewer.family_id)
    )
    if exists is None:
        db.add(ShareReport(item_id=item.id, family_id=viewer.family_id))
        item.report_count += 1
        if item.report_count >= REPORT_HIDE_THRESHOLD:
            item.status = "hidden"
        db.commit()
    return share_out(item)
