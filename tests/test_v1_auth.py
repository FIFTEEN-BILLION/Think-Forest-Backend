"""v1 인증: 카카오 웹·모바일 로그인, refresh 회전·재사용 탐지, 로그아웃, 개발용 로그인. 카카오 HTTP 는 전부 mock."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from app import db
from app.config import get_settings
from app.main import app
from app.v1 import kakao
from app.v1.deps import CurrentUser, require_user
from app.v1.models import User
from app.v1.models_auth import OAuthState, RefreshSession
from app.v1.routers import auth as auth_router
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import auth, tick

REDIRECT_URI = "https://jjcp.test/api/v1/auth/kakao/callback"
WHOAMI = "/api/v1/_test/auth-whoami"


@app.get(WHOAMI, include_in_schema=False)
def _auth_whoami(user: CurrentUser = Depends(require_user)) -> dict:
    return {"userId": user.id}


def use_settings(monkeypatch, **update):
    base = {
        "kakao_rest_api_key": "test-rest-key",
        "kakao_client_secret": "test-secret",
        "kakao_redirect_uri": REDIRECT_URI,
        "kakao_app_id": None,
        "frontend_base_url": "",
        "auth_dev_login": False,
        "auth_cookie_secure": True,
    }
    settings = get_settings().model_copy(update={**base, **update})
    monkeypatch.setattr(auth_router, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def web():
    # Secure 쿠키를 주고받으려면 https 로 부른다.
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


@pytest.fixture
def fake_kakao(monkeypatch):
    calls: dict = {"exchange": [], "users": {"kakao-token-1": 1001, "kakao-token-2": 1002}}

    def exchange_code(**kwargs):
        calls["exchange"].append(kwargs)
        if kwargs["code"] == "bad-code":
            raise kakao.KakaoError("INVALID_TOKEN")
        return {"code-a": "kakao-token-1", "code-b": "kakao-token-1", "code-c": "kakao-token-2"}[kwargs["code"]]

    def get_user_id(token):
        if token not in calls["users"]:
            raise kakao.KakaoError("INVALID_TOKEN")
        return str(calls["users"][token])

    def get_token_info(token):
        if token not in calls["users"]:
            raise kakao.KakaoError("INVALID_TOKEN")
        return kakao.TokenInfo(user_id=str(calls["users"][token]), app_id="777")

    monkeypatch.setattr(kakao, "exchange_code", exchange_code)
    monkeypatch.setattr(kakao, "get_user_id", get_user_id)
    monkeypatch.setattr(kakao, "get_token_info", get_token_info)
    return calls


def set_cookies(res) -> dict[str, str]:
    """이름 → Set-Cookie 원문(소문자)."""
    return {h.split("=", 1)[0]: h.lower() for h in res.headers.get_list("set-cookie")}


def session():
    return next(db.get_session())


def start_web_login(client: TestClient, return_to: str = "/home") -> str:
    res = client.get("/api/v1/auth/kakao/authorize", params={"returnTo": return_to})
    assert res.status_code == 302
    return parse_qs(urlsplit(res.headers["location"]).query)["state"][0]


def mobile_login(client: TestClient, token: str = "kakao-token-1") -> dict:
    res = client.post("/api/v1/auth/kakao/mobile", json={"platform": "ANDROID", "kakaoAccessToken": token})
    assert res.status_code == 200, res.text
    return res.json()


# ---------- 웹 로그인 ----------


def test_authorize_redirects_to_kakao_and_stores_hashed_state(monkeypatch, web):
    use_settings(monkeypatch)
    res = web.get("/api/v1/auth/kakao/authorize", params={"returnTo": "/stories?tab=fav"})
    assert res.status_code == 302
    url = urlsplit(res.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == kakao.AUTHORIZE_URL
    query = parse_qs(url.query)
    assert query["client_id"] == ["test-rest-key"]
    assert query["redirect_uri"] == [REDIRECT_URI]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    state = query["state"][0]

    rows = session().scalars(select(OAuthState)).all()
    assert len(rows) == 1
    assert rows[0].state_hash != state and len(rows[0].state_hash) == 64
    assert rows[0].return_to == "/stories?tab=fav"


@pytest.mark.parametrize("bad", ["https://evil.test/x", "//evil.test", "/\\evil.test", "home", ""])
def test_authorize_sanitizes_return_to(monkeypatch, web, bad):
    use_settings(monkeypatch)
    assert web.get("/api/v1/auth/kakao/authorize", params={"returnTo": bad}).status_code == 302
    assert session().scalars(select(OAuthState)).one().return_to == "/"


def test_kakao_endpoints_return_503_when_not_configured(monkeypatch, web):
    use_settings(monkeypatch, kakao_rest_api_key=None, kakao_redirect_uri=None)
    for res in (
        web.get("/api/v1/auth/kakao/authorize"),
        web.get("/api/v1/auth/kakao/callback", params={"code": "code-a", "state": "x"}),
        web.post("/api/v1/auth/kakao/mobile", json={"platform": "IOS", "kakaoAccessToken": "kakao-token-1"}),
    ):
        assert res.status_code == 503
        assert res.json()["error"]["code"] == "AUTH_PROVIDER_UNAVAILABLE"


def test_callback_sets_refresh_cookie_redirects_and_reuses_same_user(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)
    state = start_web_login(web, "/home")
    res = web.get("/api/v1/auth/kakao/callback", params={"code": "code-a", "state": state})
    assert res.status_code == 302 and res.headers["location"] == "/home"
    cookie = set_cookies(res)["jjcp_refresh"]
    for attr in ("httponly", "path=/api/v1/auth", "samesite=lax", "secure", "max-age=2592000"):
        assert attr in cookie
    exchange = fake_kakao["exchange"][0]
    assert exchange["redirect_uri"] == REDIRECT_URI and exchange["client_secret"] == "test-secret"
    assert exchange["code_verifier"]

    first = web.post("/api/v1/auth/token/refresh")
    assert first.status_code == 200, first.text

    state2 = start_web_login(web, "/")
    web.get("/api/v1/auth/kakao/callback", params={"code": "code-b", "state": state2})
    second = web.post("/api/v1/auth/token/refresh")
    assert second.json()["user"]["id"] == first.json()["user"]["id"]
    assert second.json()["user"] == {"id": first.json()["user"]["id"], "role": "CHILD", "needsFirstGreeting": True}


def test_callback_cookie_without_secure_when_disabled(monkeypatch, fake_kakao):
    use_settings(monkeypatch, auth_cookie_secure=False, frontend_base_url="http://localhost:5173")
    client = TestClient(app, follow_redirects=False)
    state = start_web_login(client, "/home")
    res = client.get("/api/v1/auth/kakao/callback", params={"code": "code-a", "state": state})
    assert res.headers["location"] == "http://localhost:5173/home"
    cookie = set_cookies(res)["jjcp_refresh"]
    assert "secure" not in cookie and "httponly" in cookie


def test_callback_state_reuse_expired_or_unknown_redirects_with_login_error(monkeypatch, web, fake_kakao, frozen):
    use_settings(monkeypatch)
    state = start_web_login(web, "/home?x=1")
    assert web.get("/api/v1/auth/kakao/callback", params={"code": "code-a", "state": state}).status_code == 302
    reused = web.get("/api/v1/auth/kakao/callback", params={"code": "code-a", "state": state})
    assert reused.headers["location"] == "/home?x=1&loginError=INVALID_STATE"
    assert "jjcp_refresh" not in set_cookies(reused)

    expired_state = start_web_login(web, "/")
    tick(frozen, 601)
    expired = web.get("/api/v1/auth/kakao/callback", params={"code": "code-a", "state": expired_state})
    assert expired.headers["location"] == "/?loginError=INVALID_STATE"

    unknown = web.get("/api/v1/auth/kakao/callback", params={"code": "code-a", "state": "nope"})
    assert unknown.headers["location"] == "/?loginError=INVALID_STATE"


def test_callback_kakao_denied_and_bad_code(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)
    denied = web.get(
        "/api/v1/auth/kakao/callback", params={"error": "access_denied", "state": start_web_login(web, "/a")}
    )
    assert denied.headers["location"] == "/a?loginError=KAKAO_CANCELLED"
    bad = web.get("/api/v1/auth/kakao/callback", params={"code": "bad-code", "state": start_web_login(web, "/b")})
    assert bad.headers["location"] == "/b?loginError=KAKAO_LOGIN_FAILED"
    assert "jjcp_refresh" not in set_cookies(bad)


# ---------- 모바일 로그인 ----------


def test_mobile_login_returns_tokens_in_body_and_access_works(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)
    body = mobile_login(web)
    assert body["accessToken"].startswith("jat_") and body["refreshToken"].startswith("jrt_")
    assert body["expiresIn"] == 3600 and body["refreshExpiresIn"] == 2592000
    assert body["user"]["role"] == "CHILD" and body["user"]["needsFirstGreeting"] is True
    assert web.get(WHOAMI, headers=auth(body["accessToken"])).json()["userId"] == body["user"]["id"]
    assert mobile_login(web)["user"]["id"] == body["user"]["id"]
    assert mobile_login(web, "kakao-token-2")["user"]["id"] != body["user"]["id"]


def test_mobile_login_rejects_invalid_token_and_wrong_app(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)
    res = web.post("/api/v1/auth/kakao/mobile", json={"platform": "ANDROID", "kakaoAccessToken": "forged"})
    assert res.status_code == 401 and res.json()["error"]["code"] == "UNAUTHORIZED"

    use_settings(monkeypatch, kakao_app_id="999")
    res = web.post("/api/v1/auth/kakao/mobile", json={"platform": "ANDROID", "kakaoAccessToken": "kakao-token-1"})
    assert res.status_code == 401

    use_settings(monkeypatch, kakao_app_id="777")
    assert mobile_login(web)["accessToken"]


def test_mobile_login_kakao_outage_is_503(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)

    def down(_token):
        raise kakao.KakaoError("UNAVAILABLE")

    monkeypatch.setattr(kakao, "get_token_info", down)
    res = web.post("/api/v1/auth/kakao/mobile", json={"platform": "IOS", "kakaoAccessToken": "kakao-token-1"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "AUTH_PROVIDER_UNAVAILABLE"


# ---------- 토큰 갱신 ----------


def test_refresh_via_cookie_rotates_cookie_and_omits_refresh_token(monkeypatch, web):
    use_settings(monkeypatch, auth_dev_login=True)
    login = web.post("/api/v1/auth/dev/login", json={"deviceKey": "device-key-0001"})
    old_cookie = web.cookies.get("jjcp_refresh")

    res = web.post("/api/v1/auth/token/refresh")
    assert res.status_code == 200
    body = res.json()
    assert "refreshToken" not in body
    assert body["accessToken"] != login.json()["accessToken"]
    new_cookie = web.cookies.get("jjcp_refresh")
    assert new_cookie and new_cookie != old_cookie
    assert "httponly" in set_cookies(res)["jjcp_refresh"]
    assert web.get(WHOAMI, headers=auth(body["accessToken"])).status_code == 200


def test_refresh_via_body_returns_new_refresh_token(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)
    login = mobile_login(web)
    res = web.post("/api/v1/auth/token/refresh", json={"refreshToken": login["refreshToken"]})
    assert res.status_code == 200
    body = res.json()
    assert body["refreshToken"].startswith("jrt_") and body["refreshToken"] != login["refreshToken"]
    assert "set-cookie" not in res.headers
    assert body["user"]["id"] == login["user"]["id"]


def test_refresh_without_token_or_with_unknown_token_is_401(monkeypatch, web):
    use_settings(monkeypatch)
    assert web.post("/api/v1/auth/token/refresh").status_code == 401
    res = web.post("/api/v1/auth/token/refresh", json={"refreshToken": "jrt_unknown"})
    assert res.status_code == 401 and res.json()["error"]["code"] == "UNAUTHORIZED"


def test_reused_rotated_refresh_revokes_whole_session_family(monkeypatch, web, fake_kakao, frozen):
    use_settings(monkeypatch)
    login = mobile_login(web)
    rotated = web.post("/api/v1/auth/token/refresh", json={"refreshToken": login["refreshToken"]}).json()

    tick(frozen, 31)  # 동시 갱신 유예 시간 밖
    reuse = web.post("/api/v1/auth/token/refresh", json={"refreshToken": login["refreshToken"]})
    assert reuse.status_code == 401

    assert web.post("/api/v1/auth/token/refresh", json={"refreshToken": rotated["refreshToken"]}).status_code == 401
    assert web.get(WHOAMI, headers=auth(login["accessToken"])).status_code == 401
    assert web.get(WHOAMI, headers=auth(rotated["accessToken"])).status_code == 401
    assert all(row.revoked_at is not None for row in session().scalars(select(RefreshSession)))


def test_near_simultaneous_reuse_fails_without_killing_session(monkeypatch, web, fake_kakao, frozen):
    use_settings(monkeypatch)
    login = mobile_login(web)
    rotated = web.post("/api/v1/auth/token/refresh", json={"refreshToken": login["refreshToken"]}).json()
    tick(frozen, 2)
    assert web.post("/api/v1/auth/token/refresh", json={"refreshToken": login["refreshToken"]}).status_code == 401
    ok = web.post("/api/v1/auth/token/refresh", json={"refreshToken": rotated["refreshToken"]})
    assert ok.status_code == 200


def test_expired_refresh_is_401_and_cookie_cleared(monkeypatch, web, frozen):
    use_settings(monkeypatch, auth_dev_login=True)
    web.post("/api/v1/auth/dev/login", json={"deviceKey": "device-key-0001"})
    tick(frozen, 30 * 24 * 3600 + 1)
    res = web.post("/api/v1/auth/token/refresh")
    assert res.status_code == 401
    assert "max-age=0" in set_cookies(res)["jjcp_refresh"]


# ---------- 로그아웃 ----------


def test_logout_revokes_session_and_clears_cookie(monkeypatch, web):
    use_settings(monkeypatch, auth_dev_login=True)
    login = web.post("/api/v1/auth/dev/login", json={"deviceKey": "device-key-0001"}).json()
    refreshed = web.post("/api/v1/auth/token/refresh").json()
    cookie = web.cookies.get("jjcp_refresh")

    res = web.post("/api/v1/auth/logout", headers=auth(refreshed["accessToken"]))
    assert res.status_code == 200 and res.json() == {"ok": True}
    cleared = set_cookies(res)["jjcp_refresh"]
    assert "max-age=0" in cleared and "path=/api/v1/auth" in cleared

    assert web.get(WHOAMI, headers=auth(refreshed["accessToken"])).status_code == 401
    assert web.get(WHOAMI, headers=auth(login["accessToken"])).status_code == 401
    web.cookies.set("jjcp_refresh", cookie, domain="testserver", path="/api/v1/auth")
    assert web.post("/api/v1/auth/token/refresh").status_code == 401


def test_logout_with_body_token_and_kakao_logout_unsupported(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)
    login = mobile_login(web)
    headers = auth(login["accessToken"])
    bad = web.post("/api/v1/auth/logout", json={"logoutFromKakao": True}, headers=headers)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_INPUT"
    assert web.post("/api/v1/auth/logout", json={"refreshToken": login["refreshToken"]}, headers=headers).json() == {
        "ok": True
    }
    assert web.post("/api/v1/auth/token/refresh", json={"refreshToken": login["refreshToken"]}).status_code == 401
    assert web.post("/api/v1/auth/logout", headers=headers).status_code == 401


def test_logout_ignores_other_users_refresh_token(monkeypatch, web, fake_kakao):
    use_settings(monkeypatch)
    mine = mobile_login(web, "kakao-token-1")
    other = mobile_login(web, "kakao-token-2")
    web.post("/api/v1/auth/logout", json={"refreshToken": other["refreshToken"]}, headers=auth(mine["accessToken"]))
    assert web.post("/api/v1/auth/token/refresh", json={"refreshToken": other["refreshToken"]}).status_code == 200


# ---------- 개발용 로그인 ----------


def test_dev_login_is_404_when_disabled(monkeypatch, web):
    use_settings(monkeypatch, auth_dev_login=False)
    res = web.post("/api/v1/auth/dev/login", json={"deviceKey": "device-key-0001"})
    assert res.status_code == 404
    assert "jjcp_refresh" not in set_cookies(res)


def test_dev_login_same_device_same_tester_user(monkeypatch, web):
    use_settings(monkeypatch, auth_dev_login=True)
    first = web.post("/api/v1/auth/dev/login", json={"deviceKey": "device-key-0001", "nickname": "테스터"})
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"accessToken", "expiresIn", "refreshExpiresIn", "user"}
    assert body["user"]["needsFirstGreeting"] is True
    cookie = set_cookies(first)["jjcp_refresh"]
    assert "httponly" in cookie and "path=/api/v1/auth" in cookie and "samesite=lax" in cookie

    assert web.get(WHOAMI, headers=auth(body["accessToken"])).json()["userId"] == body["user"]["id"]
    again = web.post("/api/v1/auth/dev/login", json={"deviceKey": "device-key-0001"}).json()
    assert again["user"]["id"] == body["user"]["id"]
    other = web.post("/api/v1/auth/dev/login", json={"deviceKey": "device-key-0002"}).json()
    assert other["user"]["id"] != body["user"]["id"]
    assert session().get(User, body["user"]["id"]).is_tester is True


def test_dev_login_validates_device_key(monkeypatch, web):
    use_settings(monkeypatch, auth_dev_login=True)
    res = web.post("/api/v1/auth/dev/login", json={"deviceKey": "short"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_INPUT"


# ---------- 카카오 HTTP 클라이언트 ----------


def fake_http(monkeypatch, handler):
    def request(method, url, **kwargs):
        return handler(method, url, kwargs)

    monkeypatch.setattr(httpx, "request", request)


def test_kakao_client_exchange_sends_form_and_parses_user(monkeypatch):
    seen = []

    def handler(method, url, kwargs):
        seen.append((method, url, kwargs))
        if url == kakao.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "kakao-at", "token_type": "bearer"})
        if url == kakao.USER_ME_URL:
            return httpx.Response(200, json={"id": 123456789, "connected_at": "2026-09-17T00:00:00Z"})
        return httpx.Response(200, json={"id": 123456789, "expires_in": 100, "app_id": 777})

    fake_http(monkeypatch, handler)
    token = kakao.exchange_code(
        client_id="cid", client_secret="sec", redirect_uri=REDIRECT_URI, code="c", code_verifier="v"
    )
    assert token == "kakao-at"
    method, _, kwargs = seen[0]
    assert method == "POST" and kwargs["data"] == {
        "grant_type": "authorization_code",
        "client_id": "cid",
        "redirect_uri": REDIRECT_URI,
        "code": "c",
        "code_verifier": "v",
        "client_secret": "sec",
    }
    assert kwargs["timeout"] is kakao.TIMEOUT
    assert kakao.get_user_id("kakao-at") == "123456789"
    assert kakao.get_token_info("kakao-at") == kakao.TokenInfo(user_id="123456789", app_id="777")
    assert seen[1][2]["headers"] == {"Authorization": "Bearer kakao-at"}


def test_kakao_client_maps_errors(monkeypatch):
    fake_http(monkeypatch, lambda *_: httpx.Response(401, json={"code": -401, "msg": "invalid token"}))
    with pytest.raises(kakao.KakaoError) as invalid:
        kakao.get_token_info("x")
    assert invalid.value.code == "INVALID_TOKEN"

    fake_http(monkeypatch, lambda *_: httpx.Response(400, json={"code": -1, "msg": "internal error"}))
    with pytest.raises(kakao.KakaoError) as busy:
        kakao.get_token_info("x")
    assert busy.value.code == "UNAVAILABLE"

    def boom(*_):
        raise httpx.ConnectTimeout("timeout")

    fake_http(monkeypatch, boom)
    with pytest.raises(kakao.KakaoError) as down:
        kakao.get_user_id("x")
    assert down.value.code == "UNAVAILABLE" and "x" not in str(down.value)
