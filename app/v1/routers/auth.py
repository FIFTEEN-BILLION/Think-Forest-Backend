"""v1 인증 — 카카오 웹·모바일 로그인, JJCP 토큰 갱신·로그아웃, 개발용 로그인.

- access `jat_`(1시간), refresh `jrt_`(30일, 사용할 때마다 회전). 원문은 저장하지 않는다.
- 웹은 같은 출처라 refresh 를 HttpOnly 쿠키(`jjcp_refresh`)에만 두고 본문에는 넣지 않는다.
- 본문으로 refreshToken 을 보낸 경우(모바일)만 본문에 새 refreshToken 을 준다.
- 이미 회전된 refresh 가 다시 오면 그 세션 계열(chain) 전체를 폐기한다(탈취 대응).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Body, Cookie, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from ... import clock
from ...auth import hash_token
from ...config import Settings, get_settings
from ...db import get_session
from ...models import Child
from ...schemas.common import CamelModel
from .. import guests, kakao, models_accounts, profile_status
from ..accounts import ACCESS_TTL_SECONDS, create_account, issue_access_token
from ..deps import CurrentUser, require_user
from ..errors import ApiError, error_body
from ..models import AccessToken, User
from ..models_accounts import LoginIdempotency
from ..models_auth import AuthIdentity, OAuthState, RefreshSession
from ..models_conversation import ChildProfile

router = APIRouter(prefix="/auth", tags=["v1-auth"])

REFRESH_TTL_SECONDS = 30 * 24 * 3600
STATE_TTL_SECONDS = 600
# 여러 탭·React StrictMode 가 같은 쿠키로 거의 동시에 갱신하는 경우. 이 안의 재사용은 401 만 주고 계열은 살린다.
REUSE_GRACE_SECONDS = 30
# 모바일 로그인 재시도용 `Idempotency-Key` 유효 시간. 이 안의 재시도는 새 refresh 계열을 만들지 않는다.
LOGIN_IDEMPOTENCY_TTL_SECONDS = 600
COOKIE_NAME = "jjcp_refresh"
COOKIE_PATH = "/api/v1/auth"


# ---------- 요청·응답 ----------


class AuthUser(CamelModel):
    id: str
    role: str
    needs_first_greeting: bool


class TokenResponse(CamelModel):
    access_token: str
    expires_in: int = ACCESS_TTL_SECONDS
    # 본문 방식(모바일)일 때만. 쿠키 방식(웹)이면 키 자체를 뺀다.
    refresh_token: str | None = None
    refresh_expires_in: int = REFRESH_TTL_SECONDS
    user: AuthUser


class KakaoExchangeRequest(CamelModel):
    code: str = Field(min_length=1, max_length=1024)
    state: str = Field(min_length=1, max_length=256)
    redirect_uri: str = Field(min_length=1, max_length=2048)


class KakaoExchangeResponse(TokenResponse):
    return_to: str


class MobileDevice(CamelModel):
    installation_id: str | None = Field(default=None, max_length=128)
    app_version: str | None = Field(default=None, max_length=32)


class MobileLoginRequest(CamelModel):
    platform: Literal["ANDROID", "IOS"]
    kakao_access_token: str = Field(min_length=1, max_length=4096)
    device: MobileDevice | None = None


class RefreshRequest(CamelModel):
    refresh_token: str | None = Field(default=None, max_length=256)


class LogoutRequest(CamelModel):
    refresh_token: str | None = Field(default=None, max_length=256)
    logout_from_kakao: bool = False


class LogoutResponse(CamelModel):
    ok: bool = True


class DevLoginRequest(CamelModel):
    device_key: str = Field(min_length=8, max_length=128)
    nickname: str | None = Field(default=None, max_length=20)


# ---------- 웹 로그인 ----------


@router.get("/kakao/authorize", response_class=RedirectResponse, status_code=302)
def kakao_authorize(
    request: Request,
    return_to: str = Query(default="/", alias="returnTo", max_length=2048),
    redirect_uri: str | None = Query(default=None, alias="redirectUri", min_length=1, max_length=2048),
    db: Session = Depends(get_session),
):
    settings = _kakao_settings()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    db.add(
        OAuthState(
            state_hash=hash_token(state),
            return_to=safe_return_to(return_to),
            code_verifier=verifier,
            expires_at=clock.now() + timedelta(seconds=STATE_TTL_SECONDS),
        )
    )
    db.commit()
    url = kakao.authorize_url(
        client_id=settings.kakao_rest_api_key or "",
        redirect_uri=redirect_uri or str(request.url_for("kakao_callback")),
        state=state,
        code_challenge=challenge,
    )
    return RedirectResponse(url, status_code=302)


@router.get("/kakao/callback", response_class=RedirectResponse, status_code=302)
def kakao_callback(
    request: Request,
    code: str | None = Query(default=None, max_length=1024),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=128),
    db: Session = Depends(get_session),
):
    settings = _kakao_settings()
    now = clock.now()
    saved = db.scalar(select(OAuthState).where(OAuthState.state_hash == hash_token(state))) if state else None
    if saved is None:
        return _login_error(settings, "/", "INVALID_STATE")
    return_to = saved.return_to
    if saved.used_at is not None or saved.expires_at <= now:
        return _login_error(settings, return_to, "INVALID_STATE")
    saved.used_at = now  # 1회용: 외부 호출 전에 먼저 소모한다.
    verifier = saved.code_verifier
    db.commit()

    if error:
        reason = "KAKAO_CANCELLED" if error == "access_denied" else "KAKAO_LOGIN_FAILED"
        return _login_error(settings, return_to, reason)
    if not code:
        return _login_error(settings, return_to, "KAKAO_LOGIN_FAILED")
    try:
        kakao_token = kakao.exchange_code(
            client_id=settings.kakao_rest_api_key or "",
            client_secret=settings.kakao_client_secret,
            redirect_uri=str(request.url_for("kakao_callback")),
            code=code,
            code_verifier=verifier,
        )
        kakao_user_id = kakao.get_user_id(kakao_token)
    except kakao.KakaoError as exc:
        reason = "AUTH_PROVIDER_UNAVAILABLE" if exc.code == "UNAVAILABLE" else "KAKAO_LOGIN_FAILED"
        return _login_error(settings, return_to, reason)

    user = _find_or_create_user(db, "KAKAO", kakao_user_id)
    if user is None:
        return _login_error(settings, return_to, "ACCOUNT_UNAVAILABLE")
    refresh_raw, _ = _start_session(db, user, platform="WEB")
    db.commit()
    response = RedirectResponse(f"{settings.frontend_base_url}{return_to}", status_code=302)
    _set_refresh_cookie(response, refresh_raw, settings)
    return response


# 프론트 콜백이 받은 인가 코드를 서버에서 교환한다. REST API 키와 Client Secret은
# 브라우저 번들에 넣지 않으며, redirectUri는 프론트가 보낸 값을 카카오에 그대로 전달한다.
@router.post("/kakao/exchange", response_model=KakaoExchangeResponse, response_model_exclude_none=True)
def kakao_exchange(
    req: KakaoExchangeRequest,
    response: Response,
    db: Session = Depends(get_session),
) -> KakaoExchangeResponse:
    settings = _kakao_settings()
    now = clock.now()
    saved = db.scalar(select(OAuthState).where(OAuthState.state_hash == hash_token(req.state)))
    if saved is None or saved.used_at is not None or saved.expires_at <= now:
        raise ApiError(400, "INVALID_STATE", "로그인 요청이 만료됐어요. 다시 시작해 주세요.")

    # state와 인가 코드는 한 번만 쓴다. 외부 호출 전에 먼저 소모해 재전송을 막는다.
    saved.used_at = now
    verifier = saved.code_verifier
    return_to = saved.return_to
    db.commit()

    try:
        kakao_token = kakao.exchange_code(
            client_id=settings.kakao_rest_api_key or "",
            client_secret=settings.kakao_client_secret,
            redirect_uri=req.redirect_uri,
            code=req.code,
            code_verifier=verifier,
        )
        kakao_user_id = kakao.get_user_id(kakao_token)
    except kakao.KakaoError as exc:
        if exc.code == "UNAVAILABLE":
            raise _provider_unavailable() from None
        raise ApiError(401, "KAKAO_LOGIN_FAILED", "카카오 로그인을 다시 해 주세요.") from None

    user = _find_or_create_user(db, "KAKAO", kakao_user_id)
    if user is None:
        raise ApiError(401, "ACCOUNT_UNAVAILABLE", "다시 로그인해 주세요.")
    refresh_raw, chain_id = _start_session(db, user, platform="WEB")
    access = issue_access_token(db, user, refresh_session_id=chain_id)
    token = _token_response(db, user, access, None)
    body = KakaoExchangeResponse(**token.model_dump(), return_to=return_to)
    db.commit()
    _set_refresh_cookie(response, refresh_raw, settings)
    return body


# ---------- 모바일 로그인 ----------


@router.post("/kakao/mobile", response_model=TokenResponse, response_model_exclude_none=True)
def kakao_mobile(
    req: MobileLoginRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_session),
) -> TokenResponse:
    settings = get_settings()
    if not settings.kakao_rest_api_key:
        raise _provider_unavailable()
    try:
        info = kakao.get_token_info(req.kakao_access_token)
        if settings.kakao_app_id and info.app_id != settings.kakao_app_id:
            raise kakao.KakaoError("INVALID_TOKEN")
        kakao_user_id = kakao.get_user_id(req.kakao_access_token)
        if kakao_user_id != info.user_id:
            raise kakao.KakaoError("INVALID_TOKEN")
    except kakao.KakaoError as exc:
        if exc.code == "UNAVAILABLE":
            raise _provider_unavailable() from None
        raise ApiError(401, "UNAUTHORIZED", "카카오 로그인을 다시 해 주세요.") from None

    user = _find_or_create_user(db, "KAKAO", kakao_user_id)
    if user is None:
        raise ApiError(401, "UNAUTHORIZED", "다시 로그인해 주세요.")
    refresh_raw, chain_id, replayed = _login_session(db, user, idempotency_key, platform=req.platform)
    access = issue_access_token(db, user, refresh_session_id=chain_id)
    body = _token_response(db, user, access, refresh_raw)
    db.commit()
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return body


# ---------- 토큰 갱신·로그아웃 ----------


@router.post("/token/refresh", response_model=TokenResponse, response_model_exclude_none=True)
def refresh_token(
    request: Request,
    response: Response,
    req: RefreshRequest | None = Body(default=None),
    cookie_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_session),
) -> TokenResponse | JSONResponse:
    settings = get_settings()
    body_token = req.refresh_token if req else None
    use_cookie = not body_token
    raw = body_token or cookie_token
    if not raw:
        return _refresh_failed(request, use_cookie, settings)

    now = clock.now()
    current = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_token(raw)))
    user = db.get(User, current.user_id) if current else None
    if current is None or user is None or user.status != "ACTIVE":
        return _refresh_failed(request, use_cookie, settings)
    if user.role == "GUEST" and guests.seconds_left(user) <= 0:
        return _refresh_failed(request, use_cookie, settings)
    if current.revoked_at is not None or current.expires_at <= now:
        return _refresh_failed(request, use_cookie, settings)

    # 동시에 같은 토큰으로 두 번 회전하지 않도록 조건부 UPDATE 로 선점한다.
    claimed = db.execute(
        update(RefreshSession)
        .where(
            RefreshSession.id == current.id,
            RefreshSession.rotated_at.is_(None),
            RefreshSession.revoked_at.is_(None),
        )
        .values(rotated_at=now)
    ).rowcount
    if claimed != 1:
        db.refresh(current)
        rotated_at = current.rotated_at
        if rotated_at is not None and now - rotated_at > timedelta(seconds=REUSE_GRACE_SECONDS):
            _revoke_chain(db, current.chain_id, now)
            db.commit()
        return _refresh_failed(request, use_cookie, settings)

    new_raw = _new_refresh(db, user, chain_id=current.chain_id, platform=current.platform)
    access = issue_access_token(db, user, refresh_session_id=current.chain_id)
    body = _token_response(db, user, access, None if use_cookie else new_raw)
    db.commit()
    if use_cookie:
        _set_refresh_cookie(response, new_raw, settings, max_age=body.refresh_expires_in)
    return body


@router.post("/logout", response_model=LogoutResponse)
def logout(
    response: Response,
    req: LogoutRequest | None = Body(default=None),
    cookie_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    authorization: str | None = Header(default=None),
    current: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> LogoutResponse:
    if req and req.logout_from_kakao:
        message = "카카오 계정 로그아웃은 아직 지원하지 않아요."
        raise ApiError(400, "INVALID_INPUT", message, {"fields": ["logoutFromKakao"]})
    now = clock.now()
    raw = (req.refresh_token if req else None) or cookie_token
    session = db.scalar(select(RefreshSession).where(RefreshSession.token_hash == hash_token(raw))) if raw else None
    if session is not None and session.user_id == current.id:
        _revoke_chain(db, session.chain_id, now)
    # 요청에 쓴 access token 은 refresh 없이 왔어도 폐기한다.
    access_raw = (authorization or "").removeprefix("Bearer ").strip()
    db.execute(
        update(AccessToken)
        .where(AccessToken.token_hash == hash_token(access_raw), AccessToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.commit()
    _clear_refresh_cookie(response, get_settings())
    return LogoutResponse()


# ---------- 공개 게스트 체험 ----------


@router.post("/guest", response_model=TokenResponse, response_model_exclude_none=True)
def guest_login(
    response: Response,
    cookie_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_session),
) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    # 기존 브라우저 세션은 유지한다. 계정 ID/기기 키를 본문으로 받아 재사용하지 않는다.
    current = db.scalar(select(RefreshSession).where(
        RefreshSession.token_hash == hash_token(cookie_token),
    )) if cookie_token else None
    user = db.get(User, current.user_id) if current else None
    if (current is not None and user is not None and user.status == "ACTIVE"
            and current.revoked_at is None and current.rotated_at is None and current.expires_at > clock.now()
            and (user.role != "GUEST" or guests.seconds_left(user) > 0)):
        access = issue_access_token(db, user, refresh_session_id=current.chain_id)
        body = _token_response(db, user, access, None)
        db.commit()
        return body

    guests.consume(db, "start:global", 1000)
    guests.purge_expired(db)
    user = create_account(db, role="GUEST", nickname="체험 새싹")
    child = db.get(Child, user.child_id)
    assert child is not None
    child.permissions = {"voice": False, "browse_shared": True, "publish_request": False}
    db.add(ChildProfile(
        user_id=user.id, child_id=user.child_id, nickname="체험 새싹",
        interests=["과학", "상상"], completed_at=clock.now(),
    ))
    db.flush()
    models_accounts.ensure_membership(db, user)
    refresh_raw, chain_id = _start_session(db, user, platform="GUEST")
    access = issue_access_token(db, user, refresh_session_id=chain_id)
    body = _token_response(db, user, access, None)
    db.commit()
    _set_refresh_cookie(response, refresh_raw, get_settings(), max_age=body.refresh_expires_in)
    return body


# ---------- 개발용 로그인 ----------


@router.post("/dev/login", response_model=TokenResponse, response_model_exclude_none=True)
def dev_login(req: DevLoginRequest, response: Response, db: Session = Depends(get_session)) -> TokenResponse:
    settings = get_settings()
    if not settings.auth_dev_login:
        # 꺼져 있으면 없는 경로와 똑같이 보인다.
        raise StarletteHTTPException(status_code=404)
    device_hash = hashlib.sha256(req.device_key.encode()).hexdigest()
    user = _find_or_create_user(db, "DEV", device_hash, is_tester=True, nickname=req.nickname or "")
    if user is None:
        raise ApiError(401, "UNAUTHORIZED", "다시 로그인해 주세요.")
    refresh_raw, chain_id = _start_session(db, user, platform="DEV")
    access = issue_access_token(db, user, refresh_session_id=chain_id)
    body = _token_response(db, user, access, None)
    db.commit()
    _set_refresh_cookie(response, refresh_raw, settings)
    return body


# ---------- 내부 ----------


def safe_return_to(value: str | None) -> str:
    """같은 출처 상대 경로만 허용한다. `//evil`, `/\\evil`, 제어 문자는 `/` 로 바꾼다."""
    if not value or not value.startswith("/") or value.startswith("//") or len(value) > 512:
        return "/"
    if "\\" in value or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "/"
    return value


def _kakao_settings() -> Settings:
    settings = get_settings()
    if not settings.kakao_rest_api_key:
        raise _provider_unavailable()
    return settings


def _provider_unavailable() -> ApiError:
    message = "지금은 카카오 로그인을 쓸 수 없어요. 잠시 뒤에 다시 시도해 주세요."
    return ApiError(503, "AUTH_PROVIDER_UNAVAILABLE", message)


def _refresh_failed(request: Request, use_cookie: bool, settings: Settings) -> JSONResponse:
    """401 UNAUTHORIZED. 쿠키 방식이면 더 못 쓰는 쿠키도 지운다(오류 응답에 쿠키 헤더를 실으려고 직접 만든다)."""
    response = JSONResponse(error_body(request, "UNAUTHORIZED", "다시 로그인해 주세요."), status_code=401)
    if use_cookie:
        _clear_refresh_cookie(response, settings)
    return response


def _login_error(settings: Settings, return_to: str, code: str) -> RedirectResponse:
    parts = urlsplit(return_to)
    query = urlencode([*parse_qsl(parts.query, keep_blank_values=True), ("loginError", code)])
    target = urlunsplit(("", "", parts.path, query, parts.fragment))
    return RedirectResponse(f"{settings.frontend_base_url}{target}", status_code=302)


def _login_session(db: Session, user: User, key: str | None, *, platform: str) -> tuple[str, str, bool]:
    """`Idempotency-Key` 가 있으면 같은 키의 재시도는 refresh 계열을 새로 만들지 않고 쓰던 계열을 이어 쓴다.

    토큰 원문과 응답 본문은 저장하지 않는다(계열 id 만 둔다). access·refresh 토큰은 재시도마다 새로 발급한다.
    """
    key = (key or "").strip()
    if not key:
        return (*_start_session(db, user, platform=platform), False)
    if len(key) > 128:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["Idempotency-Key"]})
    now = clock.now()
    record = db.scalar(select(LoginIdempotency).where(LoginIdempotency.user_id == user.id, LoginIdempotency.key == key))
    if record is not None and record.expires_at > now:
        return _new_refresh(db, user, chain_id=record.chain_id, platform=platform), record.chain_id, True
    refresh_raw, chain_id = _start_session(db, user, platform=platform)
    if record is None:
        db.add(
            LoginIdempotency(
                user_id=user.id,
                key=key,
                chain_id=chain_id,
                expires_at=now + timedelta(seconds=LOGIN_IDEMPOTENCY_TTL_SECONDS),
            )
        )
    else:
        record.chain_id = chain_id
        record.expires_at = now + timedelta(seconds=LOGIN_IDEMPOTENCY_TTL_SECONDS)
    db.flush()
    return refresh_raw, chain_id, False


def _find_or_create_user(
    db: Session, provider: str, provider_user_id: str, *, is_tester: bool = False, nickname: str = ""
) -> User | None:
    identity = db.scalar(
        select(AuthIdentity).where(AuthIdentity.provider == provider, AuthIdentity.provider_user_id == provider_user_id)
    )
    if identity is None:
        # 로그인 계정은 보호자·가족 계정이다. 아이 프로필은 이 계정 아래에 여러 개 붙는다.
        user = create_account(db, role="GUARDIAN", is_tester=is_tester, nickname=nickname)
        db.add(AuthIdentity(user_id=user.id, provider=provider, provider_user_id=provider_user_id))
        db.flush()
        return user
    identity.last_login_at = clock.now()
    user = db.get(User, identity.user_id)
    return user if user is not None and user.status == "ACTIVE" else None


def _new_refresh(db: Session, user: User, *, chain_id: str, platform: str) -> str:
    raw = f"jrt_{secrets.token_urlsafe(32)}"
    db.add(
        RefreshSession(
            chain_id=chain_id,
            user_id=user.id,
            token_hash=hash_token(raw),
            platform=platform,
            expires_at=clock.now() + timedelta(
                seconds=guests.seconds_left(user) if user.role == "GUEST" else REFRESH_TTL_SECONDS
            ),
        )
    )
    db.flush()
    return raw


def _start_session(db: Session, user: User, *, platform: str) -> tuple[str, str]:
    chain_id = f"rfs_{secrets.token_hex(16)}"
    return _new_refresh(db, user, chain_id=chain_id, platform=platform), chain_id


def _revoke_chain(db: Session, chain_id: str, now) -> None:
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.chain_id == chain_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.execute(
        update(AccessToken)
        .where(AccessToken.refresh_session_id == chain_id, AccessToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _token_response(db: Session, user: User, access: str, refresh: str | None) -> TokenResponse:
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        refresh_expires_in=guests.seconds_left(user) if user.role == "GUEST" else REFRESH_TTL_SECONDS,
        user=AuthUser(id=user.id, role=user.role, needs_first_greeting=profile_status.needs_first_greeting(db, user)),
    )


def _cookie_kwargs(settings: Settings) -> dict:
    return {"path": COOKIE_PATH, "secure": settings.auth_cookie_secure, "httponly": True, "samesite": "lax"}


def _set_refresh_cookie(
    response: Response, raw: str, settings: Settings, *, max_age: int = REFRESH_TTL_SECONDS
) -> None:
    response.set_cookie(COOKIE_NAME, raw, max_age=max_age, **_cookie_kwargs(settings))


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(COOKIE_NAME, **_cookie_kwargs(settings))
