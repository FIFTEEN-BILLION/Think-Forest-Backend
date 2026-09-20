"""환경변수로 명시적으로 활성화한 개발 전용 내 계정 초기화."""

from fastapi import APIRouter, Depends, Response
from pydantic import ConfigDict
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from ...config import get_settings
from ...db import get_session
from ...schemas.common import CamelModel
from ..debug_reset import erase_account
from ..deps import CurrentUser, require_user
from .auth import _clear_refresh_cookie


def require_debug() -> None:
    if not get_settings().debug_mode:
        raise HTTPException(status_code=404)


router = APIRouter(prefix="/debug", tags=["v1-debug"], dependencies=[Depends(require_debug)])


class ResetRequest(CamelModel):
    # 삭제 대상 ID는 받지 않는다. 로그인한 계정만 초기화한다.
    model_config = ConfigDict(extra="forbid")


class ResetResponse(CamelModel):
    ok: bool = True


@router.post("/reset-account", response_model=ResetResponse)
def reset_account(
    body: ResetRequest,
    response: Response,
    current: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ResetResponse:
    erase_account(db, current.user)
    db.commit()
    _clear_refresh_cookie(response, get_settings())
    response.headers["Cache-Control"] = "no-store"
    return ResetResponse()
