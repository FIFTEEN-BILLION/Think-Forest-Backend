"""이야기 공유와 보호자 승인(명세 15절).

DRAFT → PENDING_GUARDIAN → APPROVED → PUBLISHED, 그리고 REJECTED·CANCELLED·REVOKED.

- 공개본은 승인 시점의 스냅숏이다. 승인 뒤 아이가 본문을 고치면 공개를 멈추고(`PAUSED`) 다시 승인을 받는다.
- 보호자가 확인한 버전(`confirmedBodyVersion`)과 지금 본문 버전이 다르면 승인하지 않는다(409).
- 공개본에는 실제 이름·학교·정확한 나이를 넣지 않는다.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..safety import pii
from ..safety import topics as sensitive
from .cursor import iso
from .deps import CurrentUser, ProfileScope
from .errors import ApiError
from .models_conversation import ChildProfile, StoryRecord
from .models_social import PublicStory, ShareRequest
from .social_schemas import PublicStoryOut, ShareRequestOut

OPEN_REQUEST_STATUSES = ("DRAFT", "PENDING_GUARDIAN", "APPROVED", "PUBLISHED")
CANCELLABLE = ("DRAFT", "PENDING_GUARDIAN")
VISIBLE_PUBLIC = ("PUBLISHED",)
EXCERPT_LIMIT = 120

_GRADE = re.compile(r"([1-6])\s*학년")
_AGE = re.compile(r"(\d{1,2})\s*살")


def age_band(profile: ChildProfile | None) -> str:
    """정확한 학년·나이를 넓은 구간으로 바꾼다. 알 수 없으면 '또래 친구'."""
    raw = (profile.grade_or_age_band if profile else None) or ""
    if hit := _GRADE.search(raw):
        return "초등 저학년" if int(hit.group(1)) <= 3 else "초등 고학년"
    if hit := _AGE.search(raw):
        return "초등 저학년" if int(hit.group(1)) <= 9 else "초등 고학년"
    return "또래 친구"


def display_name(profile: ChildProfile | None, hide_profile: bool) -> str:
    """hideProfile 이면 별명도 감춘다. 실제 이름·학교는 어느 경우에도 쓰지 않는다."""
    if hide_profile:
        return "친구"
    nickname = (profile.nickname if profile else "") or ""
    return nickname[:20] or "친구"


def _mask(text: str) -> str:
    return pii.mask(text or "").text


def excerpt_of(body: str) -> str:
    text = " ".join((body or "").split())
    return text if len(text) <= EXCERPT_LIMIT else text[: EXCERPT_LIMIT - 1] + "…"


# --- 조회 도우미 ------------------------------------------------------------------


def own_story(db: Session, cu: CurrentUser, story_id: str) -> StoryRecord:
    story = db.get(StoryRecord, story_id)
    if story is None or story.user_id != cu.id:
        raise ApiError(404, "STORY_NOT_FOUND", "이야기를 찾을 수 없어요.")
    return story


def own_request(db: Session, cu: CurrentUser, request_id: str) -> ShareRequest:
    """URL 의 id 를 믿지 않는다. 다른 계정 것이면 있는지조차 알려 주지 않는다(404)."""
    request = db.get(ShareRequest, request_id)
    if request is None or request.user_id != cu.id:
        raise ApiError(404, "SHARE_REQUEST_NOT_FOUND", "공유 요청을 찾을 수 없어요.")
    return request


def public_of(db: Session, request: ShareRequest) -> PublicStory | None:
    return db.get(PublicStory, request.public_story_id) if request.public_story_id else None


def sync_edits(db: Session, request: ShareRequest, public: PublicStory | None) -> None:
    """승인 뒤 본문이 바뀌었으면 공개를 멈추고 다시 승인 대기로 돌린다(공개본 내용은 그대로 둔다)."""
    if public is None or public.status != "PUBLISHED":
        return
    story = db.get(StoryRecord, public.story_id)
    if story is None or story.version == public.body_version:
        return
    public.status = "PAUSED"
    public.hidden_at = clock.now()
    request.status = "PENDING_GUARDIAN"
    request.pending_reason = "STORY_EDITED"
    request.requested_body_version = story.version
    request.requested_at = clock.now()


# --- 상태 바꾸기 ------------------------------------------------------------------


def create(db: Session, cu: CurrentUser, story: StoryRecord, audience: str, hide_profile: bool) -> ShareRequest:
    existing = db.scalar(
        select(ShareRequest).where(
            ShareRequest.story_id == story.id,
            ShareRequest.user_id == cu.id,
            ShareRequest.status.in_(OPEN_REQUEST_STATUSES),
        )
    )
    if existing is not None:
        raise ApiError(409, "SHARE_ALREADY_REQUESTED", "이미 보낸 공유 요청이 있어요.", {"shareRequestId": existing.id})
    for text in (story.title, story.summary, story.body):
        if sensitive.detect(text or ""):
            raise ApiError(422, "UNSAFE_CONTENT", "이 이야기는 친구들에게 보여 주기 어려워요.")
    request = ShareRequest(
        user_id=cu.id,
        story_id=story.id,
        audience=audience,
        hide_profile=hide_profile,
        status="PENDING_GUARDIAN",
        requested_body_version=story.version,
        requested_at=clock.now(),
    )
    db.add(request)
    db.flush()
    return request


def cancel(request: ShareRequest) -> None:
    if request.status not in CANCELLABLE:
        raise ApiError(409, "SHARE_NOT_CANCELLABLE", "승인 뒤에는 취소할 수 없어요.", {"status": request.status})
    request.status = "CANCELLED"
    request.decided_at = clock.now()


def approve(db: Session, scope: ProfileScope, request: ShareRequest, version: int, redactions: bool) -> PublicStory:
    if request.status != "PENDING_GUARDIAN":
        raise ApiError(409, "SHARE_NOT_PENDING", "지금은 승인할 수 없는 요청이에요.", {"status": request.status})
    story = db.get(StoryRecord, request.story_id)
    if story is None:
        raise ApiError(404, "STORY_NOT_FOUND", "이야기를 찾을 수 없어요.")
    if version != story.version:
        raise ApiError(
            409,
            "SHARE_VERSION_MISMATCH",
            "확인하신 내용과 지금 이야기가 달라요. 다시 읽고 승인해 주세요.",
            {"confirmedBodyVersion": version, "currentBodyVersion": story.version},
        )
    profile = db.get(ChildProfile, scope.profile_id)
    request.status = "APPROVED"
    request.confirmed_body_version = version
    request.confirmed_redactions = redactions
    request.pending_reason = None
    request.decided_at = clock.now()

    public = public_of(db, request)
    if public is None:
        public = PublicStory(share_request_id=request.id, story_id=story.id, author_user_id=request.user_id)
        db.add(public)
    public.audience = request.audience
    public.display_name = display_name(profile, request.hide_profile)
    public.age_band = age_band(profile)
    public.category = story.category
    public.title = _mask(story.title)
    public.body = _mask(story.body)
    public.excerpt = excerpt_of(_mask(story.summary or story.body))
    public.thought_journey = {k: v for k, v in (story.thought_journey or {}).items()}
    public.body_version = story.version
    public.status = "PUBLISHED"
    public.hidden_at = None
    public.published_at = clock.now()
    db.flush()
    request.public_story_id = public.id
    request.status = "PUBLISHED"
    return public


def reject(db: Session, request: ShareRequest, reason: str) -> None:
    if request.status != "PENDING_GUARDIAN":
        raise ApiError(409, "SHARE_NOT_PENDING", "지금은 반려할 수 없는 요청이에요.", {"status": request.status})
    request.status = "REJECTED"
    request.reject_reason = reason
    request.decided_at = clock.now()
    public = public_of(db, request)
    if public is not None:
        public.status = "REVOKED"
        public.hidden_at = clock.now()


def revoke(db: Session, request: ShareRequest) -> None:
    if request.status not in ("APPROVED", "PUBLISHED"):
        raise ApiError(409, "SHARE_NOT_PUBLISHED", "아직 공개된 이야기가 아니에요.", {"status": request.status})
    request.status = "REVOKED"
    request.decided_at = clock.now()
    public = public_of(db, request)
    if public is not None:
        public.status = "REVOKED"
        public.hidden_at = clock.now()


# --- 응답 ------------------------------------------------------------------------


def request_out(request: ShareRequest) -> ShareRequestOut:
    return ShareRequestOut(
        id=request.id,
        story_id=request.story_id,
        status=request.status,
        audience=request.audience,
        hide_profile=request.hide_profile,
        requested_body_version=request.requested_body_version,
        confirmed_body_version=request.confirmed_body_version,
        public_story_id=request.public_story_id,
        reject_reason=request.reject_reason,
        pending_reason=request.pending_reason,
        requested_at=iso(request.requested_at) or "",
        decided_at=iso(request.decided_at),
        updated_at=iso(request.updated_at) or "",
    )


def public_out(public: PublicStory, *, recommended_by_me: bool = False, reason: str = "") -> PublicStoryOut:
    return PublicStoryOut(
        id=public.id,
        title=public.title,
        excerpt=public.excerpt,
        author={"displayName": public.display_name, "ageBand": public.age_band},
        category=public.category,
        recommendation_count=public.recommendation_count,
        recommended_by_me=recommended_by_me,
        recommendation_reason=reason or "보호자가 확인한 친구의 이야기예요.",
        guardian_approved=True,
        published_at=iso(public.published_at) or "",
    )
