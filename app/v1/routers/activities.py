"""v1 생각 모험 활동 — 목록·상세와 활동 세션(시작·복원·자동 저장·단계 이동·완료·취소).

서버가 각 단계의 조건을 다시 검사한다(`activity_rules`). 화면의 버튼 비활성화는 완료 판정에 쓰지 않는다.
완료하면 점수 없이 책장 기록(`story_records`)을 하나 만든다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from ...safety import pii
from .. import activity_catalog as catalog
from .. import activity_rules as rules
from .. import conversation_scope, cursor, idempotency
from ..activity_schemas import (
    ActivityCompleteResponse,
    ActivityDetail,
    ActivityDetailResponse,
    ActivityDraft,
    ActivityItem,
    ActivityList,
    ActivityPatchRequest,
    ActivitySessionOut,
    ActivitySessionResponse,
    ActivityStartRequest,
    ActivityStep,
    ActivityStoryOut,
    ActivityVisuals,
    MissingCondition,
)
from ..cursor import iso
from ..deps import ProfileScope, require_profile
from ..errors import ApiError
from ..models_activity import ActivitySession
from ..models_conversation import ConversationSession, StoryRecord

router = APIRouter(tags=["v1-activities"])
OPEN_STATUS = "ACTIVE"


def _item(activity: catalog.Activity) -> ActivityItem:
    return ActivityItem(
        id=activity.id,
        track=activity.track,
        area=activity.area,
        place=activity.place["name"],
        title=activity.title,
        subtitle=activity.subtitle,
        level=activity.level,
        tags=list(activity.tags),
        description=activity.description,
        estimated_minutes=activity.duration,
        min_characters=activity.min_characters,
    )


@router.get("/activities", response_model=ActivityList)
def list_activities(
    query: str | None = Query(default=None, max_length=40),
    track: str | None = Query(default=None, description="forest · lab · theater"),
    area: str | None = Query(default=None, max_length=30, description="영역 이름 일부"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    scope: ProfileScope = Depends(require_profile),
):
    size = cursor.clamp_limit(limit)
    offset = cursor.decode_offset(cursor_raw)
    if track is not None and track not in catalog.TRACKS:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["track"]})
    found = [a for a in catalog.CATALOG if track is None or a.track == track]
    if area and area.strip():
        found = [a for a in found if area.strip() in a.area]
    if query and query.strip():
        q = query.strip()
        found = [
            a for a in found if q in a.title or q in a.subtitle or q in a.description or any(q in t for t in a.tags)
        ]
    page = found[offset : offset + size]
    next_cursor = cursor.encode_offset(offset + size) if len(found) > offset + size else None
    return ActivityList(items=[_item(a) for a in page], next_cursor=next_cursor)


@router.get("/activities/{activity_id}", response_model=ActivityDetailResponse)
def get_activity(activity_id: str, scope: ProfileScope = Depends(require_profile)):
    activity = catalog.BY_ID.get(activity_id)
    if activity is None:
        raise ApiError(404, "ACTIVITY_NOT_FOUND", "활동을 찾을 수 없어요.", {"activityId": activity_id})
    return ActivityDetailResponse(
        activity=ActivityDetail(
            **_item(activity).model_dump(),
            intro=catalog.intro(activity),
            clue=catalog.clue(activity),
            steps=list(activity.steps),
            questions=catalog.questions(activity),
            visuals=ActivityVisuals(**catalog.visuals(activity)),
        )
    )


# --- 활동 세션 ------------------------------------------------------------------


def _session_out(session: ActivitySession) -> ActivitySessionOut:
    activity = catalog.BY_ID[session.activity_id]
    state = session.state or {}
    total = rules.max_step(activity)
    labels = activity.steps
    open_now = session.status == OPEN_STATUS
    codes = rules.missing(activity, session.step, state, session.min_characters) if open_now else []
    return ActivitySessionOut(
        session_id=session.id,
        activity_id=session.activity_id,
        track=session.track,
        title=session.title,
        status=session.status,
        revision=session.revision,
        step=ActivityStep(
            index=session.step,
            label=labels[session.step] if session.step < len(labels) else labels[-1],
            total=total,
            writing=rules.writing_step(session.track, session.activity_id, session.step),
        ),
        min_characters=session.min_characters,
        draft=ActivityDraft(
            text=state.get("text", ""),
            answers=state.get("answers", []),
            followup=state.get("followup", ""),
            hints=state.get("hints", 0),
            lab=state.get("lab", {}),
            theater=state.get("theater", {}),
            inquiry=state.get("inquiry"),
            path=state.get("path"),
        ),
        missing=[MissingCondition(code=c, message=rules.MESSAGES[c].format(min=session.min_characters)) for c in codes],
        ready_to_complete=rules.ready_to_complete(activity, session.step, state, session.min_characters),
        story_id=session.story_id,
        started_at=iso(session.started_at) or "",
        updated_at=iso(session.updated_at) or "",
    )


def _own(db: Session, scope: ProfileScope, session_id: str) -> ActivitySession:
    """다른 프로필의 세션은 있는지도 알려 주지 않는다(404)."""
    session = db.get(ActivitySession, session_id)
    if session is None or session.profile_id != scope.profile_id:
        raise ApiError(404, "ACTIVITY_SESSION_NOT_FOUND", "활동을 찾을 수 없어요.", {"sessionId": session_id})
    return session


def _ensure_open(session: ActivitySession) -> None:
    if session.status != OPEN_STATUS:
        raise ApiError(409, "SESSION_CLOSED", "이미 끝난 활동이에요.", {"status": session.status})


def _step_error(exc: rules.StepError, session: ActivitySession) -> ApiError:
    return ApiError(
        409,
        "ACTIVITY_STEP_NOT_READY",
        exc.details[0]["message"] if exc.details else "다음 단계로 갈 수 없어요.",
        {"step": session.step, "missing": exc.codes, "conditions": exc.details},
    )


@router.post("/activity-sessions", response_model=ActivitySessionResponse, status_code=201)
def start_activity(
    req: ActivityStartRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    route = "POST /activity-sessions"
    if (replayed := idempotency.replay(db, scope.user.id, idempotency_key, route)) is not None:
        return replayed
    activity = catalog.BY_ID.get(req.activity_id)
    if activity is None:
        raise ApiError(404, "ACTIVITY_NOT_FOUND", "활동을 찾을 수 없어요.", {"activityId": req.activity_id})
    keyword = pii.mask(" ".join(req.keyword.split()), names=False).text
    if keyword and rules.blocked_keyword(keyword):
        raise ApiError(422, "UNSAFE_CONTENT", "이 키워드로는 이야기를 준비할 수 없어요. 다른 키워드를 골라 주세요.")
    now = clock.now()
    session = ActivitySession(
        user_id=scope.user.id,
        profile_id=scope.profile_id,
        activity_id=activity.id,
        track=activity.track,
        title=activity.title,
        min_characters=activity.min_characters,
        state=rules.empty_state(activity, keyword),
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.flush()
    out = ActivitySessionResponse(session=_session_out(session))
    idempotency.remember(db, scope.user.id, idempotency_key, route, out, status_code=201)
    db.commit()
    return out


@router.get("/activity-sessions/{session_id}", response_model=ActivitySessionResponse)
def get_activity_session(
    session_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    return ActivitySessionResponse(session=_session_out(_own(db, scope, session_id)))


@router.patch("/activity-sessions/{session_id}", response_model=ActivitySessionResponse)
def autosave_activity_session(
    session_id: str,
    req: ActivityPatchRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    """마지막 쓰기가 이긴다. 단 `clientRevision` 이 서버 값과 다르면 늦게 온 쓰기로 보고 409."""
    session = _own(db, scope, session_id)
    _ensure_open(session)
    if req.client_revision != session.revision:
        raise ApiError(
            409,
            "ACTIVITY_REVISION_CONFLICT",
            "다른 기기에서 먼저 저장했어요. 최신 내용을 받아 주세요.",
            {"serverRevision": session.revision, "clientRevision": req.client_revision},
        )
    event = req.event.model_dump(by_alias=False)
    if event["type"] == "TEXT" and event.get("value"):
        event["value"] = pii.mask(str(event["value"]), names=False).text
    activity = catalog.BY_ID[session.activity_id]
    try:
        session.state = rules.apply_event(activity, session.step, session.state or {}, event)
    except (rules.EventError, TypeError, ValueError) as exc:
        details = {"fields": ["event"], "reason": str(exc)}
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", details) from exc
    session.revision += 1
    session.updated_at = clock.now()
    db.commit()
    return ActivitySessionResponse(session=_session_out(session))


@router.post("/activity-sessions/{session_id}/advance", response_model=ActivitySessionResponse)
def advance_activity_session(
    session_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    session = _own(db, scope, session_id)
    _ensure_open(session)
    activity = catalog.BY_ID[session.activity_id]
    try:
        session.step, session.state = rules.advance(activity, session.step, session.state or {}, session.min_characters)
    except rules.StepError as exc:
        raise _step_error(exc, session) from exc
    session.title = (session.state or {}).get("title", session.title)
    session.revision += 1
    session.updated_at = clock.now()
    db.commit()
    return ActivitySessionResponse(session=_session_out(session))


def _story_body(activity: catalog.Activity, answers: list[dict]) -> str:
    lines = [f"{a.get('question', '')}\n{a.get('text', '')}".strip() for a in answers]
    return f"{activity.title}\n\n" + "\n\n".join(line for line in lines if line)


@router.post("/activity-sessions/{session_id}/complete", response_model=ActivityCompleteResponse)
def complete_activity_session(
    session_id: str,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    """완료하고 책장 기록을 만든다. 점수는 만들지 않는다. 같은 세션을 다시 완료하면 처음 결과를 그대로 돌려준다."""
    route = "POST /activity-sessions/complete"
    if (replayed := idempotency.replay(db, scope.user.id, idempotency_key, route)) is not None:
        return replayed
    session = _own(db, scope, session_id)
    if session.status == "COMPLETED" and session.result:
        return ActivityCompleteResponse.model_validate(session.result)
    _ensure_open(session)
    activity = catalog.BY_ID[session.activity_id]
    state = session.state or {}
    if not rules.ready_to_complete(activity, session.step, state, session.min_characters):
        codes = rules.missing(activity, session.step, state, session.min_characters) or ["STEP_DONE"]
        raise _step_error(rules.StepError(codes, session.min_characters), session)

    now = clock.now()
    answers = rules.record_answers(activity, state)
    category = catalog.CATEGORY_OF_TRACK[activity.track]
    # story_records.session_id 는 conversation_sessions 를 가리킨다. 기존 테이블을 바꾸지 않으려고
    # 활동 한 건당 kind="ACTIVITY" 짝 행을 하나 만들어 외래키를 맞춘다(대화 목록·홈은 kind 로 걸러 영향 없음).
    anchor = ConversationSession(
        id=f"cnva_{session.id}",
        user_id=session.user_id,
        kind="ACTIVITY",
        topic={"apiId": f"activity_{activity.id}", "title": session.title, "apiCategory": category},
        status="COMPLETED",
        readiness={},
        current_interaction=None,
        last_message_at=now,
        completed_at=now,
        created_at=session.started_at,
        updated_at=now,
    )
    db.add(anchor)
    db.flush()
    conversation_scope.bind(db, anchor.id, scope.child.id)
    story = StoryRecord(
        user_id=session.user_id,
        session_id=anchor.id,
        category=category,
        topic_id=f"activity_{activity.id}",
        topic_title=session.title[:80],
        title=session.title[:80],
        summary=f"‘{session.title}’ 활동을 마치며 직접 살펴보고 생각한 것을 남겼어요.",
        body=_story_body(activity, answers),
        thought_journey=rules.thought_journey(activity, state, answers),
        ai_original=None,
        source="fallback",
        favorite=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(story)
    db.flush()
    session.status = "COMPLETED"
    session.story_id = story.id
    session.completed_at = now
    session.updated_at = now
    session.revision += 1
    anchor.story_id = story.id
    out = ActivityCompleteResponse(
        session=_session_out(session),
        story=ActivityStoryOut(
            id=story.id,
            title=story.title,
            summary=story.summary,
            body=story.body,
            category=story.category,
            answers=answers,
            created_at=iso(story.created_at) or "",
        ),
    )
    session.result = out.model_dump(mode="json", by_alias=True)
    idempotency.remember(db, scope.user.id, idempotency_key, route, out)
    db.commit()
    return out


@router.delete("/activity-sessions/{session_id}", status_code=204)
def cancel_activity_session(
    session_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    session = _own(db, scope, session_id)
    if session.status == "COMPLETED":
        raise ApiError(409, "SESSION_CLOSED", "이미 끝난 활동이에요.", {"status": session.status})
    if session.status == OPEN_STATUS:
        now = clock.now()
        session.status = "CANCELLED"
        session.cancelled_at = now
        session.updated_at = now
        db.commit()
    return Response(status_code=204)


def open_sessions(db: Session, profile_id: str) -> list[ActivitySession]:
    """진행 중인 활동(홈·리포트에서 쓴다)."""
    return list(
        db.scalars(
            select(ActivitySession)
            .where(ActivitySession.profile_id == profile_id, ActivitySession.status == OPEN_STATUS)
            .order_by(ActivitySession.updated_at.desc())
        )
    )


@router.get("/activity-sessions")
def list_sessions(
    status: str = Query(default="ACTIVE"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    """프로필별 저장한 활동 목록. 홈·활동 선택에서 이어하기에 쓴다."""
    if status not in ("ACTIVE", "COMPLETED", "CANCELLED"):
        raise ApiError(400, "INVALID_INPUT", "활동 상태를 확인해 주세요.")
    size, offset = cursor.clamp_limit(limit), cursor.decode_offset(cursor_raw)
    rows = list(
        db.scalars(
            select(ActivitySession)
            .where(
                ActivitySession.profile_id == scope.profile_id,
                ActivitySession.status == status,
            )
            .order_by(ActivitySession.updated_at.desc(), ActivitySession.id.desc())
            .offset(offset)
            .limit(size + 1)
        )
    )
    return {
        "items": [_session_out(row).model_dump(mode="json", by_alias=True) for row in rows[:size]],
        "nextCursor": cursor.encode_offset(offset + size) if len(rows) > size else None,
    }
