"""카카오 로그인 REST 호출. 테스트는 이 모듈의 함수를 바꿔 끼운다.

근거: https://developers.kakao.com/docs/ko/kakaologin/rest-api (2026-09-17 확인)
- 인가: GET https://kauth.kakao.com/oauth/authorize (client_id, redirect_uri, response_type=code, state)
- 토큰: POST https://kauth.kakao.com/oauth/token (form: grant_type, client_id, redirect_uri, code, client_secret?)
- PKCE: OIDC 디스커버리(`/.well-known/openid-configuration`)가 code_challenge_methods_supported=["S256"] 을 알린다.
- 사용자: GET https://kapi.kakao.com/v2/user/me → `id`(Long, 회원번호)
- 토큰 정보: GET https://kapi.kakao.com/v1/user/access_token_info → `id`, `expires_in`, `app_id`. 무효 토큰은 401(-401).

카카오 토큰·인가 코드는 로그에 남기지 않고, 예외 메시지에도 넣지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
USER_ME_URL = "https://kapi.kakao.com/v2/user/me"
TOKEN_INFO_URL = "https://kapi.kakao.com/v1/user/access_token_info"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class KakaoError(Exception):
    """INVALID_TOKEN(토큰·코드가 무효) 또는 UNAVAILABLE(네트워크·카카오 장애)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TokenInfo:
    user_id: str
    app_id: str


def authorize_url(*, client_id: str, redirect_uri: str, state: str, code_challenge: str) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def exchange_code(
    *, client_id: str, client_secret: str | None, redirect_uri: str, code: str, code_verifier: str
) -> str:
    """인가 코드를 카카오 access token 으로 바꾼다."""
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": code,
        "code_verifier": code_verifier,
    }
    if client_secret:
        form["client_secret"] = client_secret
    body = _call("POST", TOKEN_URL, data=form, invalid_statuses=(400, 401))
    token = body.get("access_token")
    if not isinstance(token, str) or not token:
        raise KakaoError("UNAVAILABLE")
    return token


def get_user_id(access_token: str) -> str:
    body = _call("GET", USER_ME_URL, headers=_bearer(access_token), invalid_statuses=(401,))
    return _id(body.get("id"))


def get_token_info(access_token: str) -> TokenInfo:
    body = _call("GET", TOKEN_INFO_URL, headers=_bearer(access_token), invalid_statuses=(400, 401))
    return TokenInfo(user_id=_id(body.get("id")), app_id=str(body.get("app_id", "")))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | str) or str(value) == "":
        raise KakaoError("UNAVAILABLE")
    return str(value)


def _call(method: str, url: str, *, invalid_statuses: tuple[int, ...], **kwargs) -> dict:
    try:
        resp = httpx.request(method, url, timeout=TIMEOUT, **kwargs)
    except httpx.HTTPError:
        raise KakaoError("UNAVAILABLE") from None
    if resp.status_code in invalid_statuses:
        # 400 중 -1(카카오 내부 장애)은 재시도 대상이다.
        if resp.status_code == 400 and _kakao_code(resp) == -1:
            raise KakaoError("UNAVAILABLE")
        raise KakaoError("INVALID_TOKEN")
    if resp.status_code != 200:
        raise KakaoError("UNAVAILABLE")
    try:
        body = resp.json()
    except ValueError:
        raise KakaoError("UNAVAILABLE") from None
    if not isinstance(body, dict):
        raise KakaoError("UNAVAILABLE")
    return body


def _kakao_code(resp: httpx.Response) -> int | None:
    try:
        code = resp.json().get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, int) else None
