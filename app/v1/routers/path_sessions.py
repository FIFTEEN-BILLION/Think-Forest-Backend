"""로그인한 아이의 길찾기: 말·프로그램·실행 결과를 활동 세션에 저장한다."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from ...routers.path import react_program, teach_program
from ...schemas.common import CamelModel
from ...schemas.path import PathReactRequest, PathTeachRequest
from ...services import usage
from .. import ai_gate, path_engine
from ..activity_schemas import ActivitySessionResponse
from ..deps import ProfileScope, require_profile
from ..errors import ApiError
from ..library_common import child_text, current_user
from ..models_activity import ActivitySession
from .activities import _ensure_open, _own, _session_out

router = APIRouter(tags=["v1-activities"])


class PathRevision(CamelModel):
    client_revision: int = Field(ge=0)


class PathText(PathRevision):
    text: str = Field(min_length=1, max_length=1000)


def _prepare(db, scope, session_id, revision):
    session = _own(db, scope, session_id)
    _ensure_open(session)
    if session.activity_id != "path-teaching" or session.step != 0:
        raise ApiError(409, "SESSION_CLOSED", "길찾기 활동의 진행 단계를 확인해 주세요.")
    if session.revision != revision:
        raise ApiError(409, "ACTIVITY_REVISION_CONFLICT", "다른 곳에서 저장했어요. 새로 불러와 주세요.")
    return session, dict(session.state.get("path") or {})


def _reason(scope):
    reason = ai_gate.block_reason(scope.child)
    if reason:
        return reason
    return None if usage.try_consume(scope.child.id) else "daily_limit"


def _save(db, session, path):
    result = db.execute(
        update(ActivitySession)
        .where(
            ActivitySession.id == session.id,
            ActivitySession.revision == session.revision,
            ActivitySession.status == "ACTIVE",
        )
        .values(state={**session.state, "path": path}, revision=session.revision + 1, updated_at=clock.now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        raise ApiError(409, "ACTIVITY_REVISION_CONFLICT", "다른 곳에서 저장했어요. 새로 불러와 주세요.")
    db.commit()
    db.refresh(session)
    return ActivitySessionResponse(session=_session_out(session))


@router.post("/activity-sessions/{session_id}/path/teach", response_model=ActivitySessionResponse)
def teach_path(
    session_id: str, req: PathText, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    session, path = _prepare(db, scope, session_id, req.client_revision)
    text = child_text(db, current_user(scope), req.text, 1000)
    clarify = path.get("clarify")
    body = PathTeachRequest.model_validate(
        {
            "text": text,
            "program": path.get("program", []),
            "mapId": path["map"]["id"],
            "attempt": path.get("runs", 0),
            "inputOrigin": "child",
            "pendingClarify": {"question": clarify["question"], "chosen": text} if clarify else None,
        }
    )
    result = teach_program(body, _reason(scope))
    path["program"] = [s.model_dump(by_alias=True) for s in result.program]
    path["clarify"] = result.clarify.model_dump(by_alias=True) if result.clarify else None
    path["turns"] = [*path.get("turns", []), {"text": text, "reply": result.tiki_line}][-40:]
    return _save(db, session, path)


@router.post("/activity-sessions/{session_id}/path/run", response_model=ActivitySessionResponse)
def run_path(
    session_id: str,
    req: PathRevision,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    session, path = _prepare(db, scope, session_id, req.client_revision)
    if not path.get("program"):
        raise ApiError(409, "ACTIVITY_STEP_NOT_READY", "먼저 티키에게 길을 알려 주세요.")
    result = path_engine.run(path["program"], path["map"])
    response = react_program(
        PathReactRequest.model_validate(
            {
                "program": path["program"],
                "mapId": path["map"]["id"],
                "attempt": path.get("runs", 0),
                "inputOrigin": "child",
                "result": {"outcome": result["outcome"], "moves": len(result["cells"]) - 1},
            }
        ),
        _reason(scope),
    )
    path["lastRun"] = result
    path["runs"] = int(path.get("runs", 0)) + 1
    path["wins"] = int(path.get("wins", 0)) + int(result["outcome"] == "arrived")
    path["turns"] = [*path.get("turns", []), {"text": "티키 출발!", "reply": response.tiki_line}][-40:]
    path["awaitingChallenge"] = False
    return _save(db, session, path)
