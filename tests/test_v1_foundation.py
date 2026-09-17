"""v1 공통 기반: 오류 형식, 요청 ID, access token 인증."""

from __future__ import annotations

from datetime import timedelta

from app import clock, db
from app.main import app
from app.v1.accounts import create_account, issue_access_token
from app.v1.deps import CurrentUser, require_user
from fastapi import Depends
from fastapi.testclient import TestClient


@app.get("/api/v1/_test/whoami", include_in_schema=False)
def _whoami(user: CurrentUser = Depends(require_user)) -> dict:
    return {"userId": user.id, "childId": user.child.id}


def _token(**kwargs) -> tuple[str, str]:
    session = next(db.get_session())
    user = create_account(session, **kwargs)
    raw = issue_access_token(session, user)
    session.commit()
    return raw, user.id


def test_missing_token_uses_v1_error_shape_and_request_id():
    res = TestClient(app).get("/api/v1/_test/whoami", headers={"X-Request-Id": "req_abcdefgh1234"})
    assert res.status_code == 401
    assert res.headers["X-Request-Id"] == "req_abcdefgh1234"
    assert res.json() == {
        "error": {"code": "UNAUTHORIZED", "message": "다시 로그인해 주세요.", "details": {}, "requestId": "req_abcdefgh1234"}
    }


def test_valid_token_resolves_user_and_expired_token_fails(monkeypatch):
    raw, user_id = _token(is_tester=True, nickname="별")
    client = TestClient(app)
    ok = client.get("/api/v1/_test/whoami", headers={"Authorization": f"Bearer {raw}"})
    assert ok.status_code == 200 and ok.json()["userId"] == user_id
    later = clock.now() + timedelta(hours=2)
    monkeypatch.setattr(clock, "now", lambda: later)
    assert client.get("/api/v1/_test/whoami", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_old_routes_keep_their_error_format():
    res = TestClient(app).get("/talks/today")
    assert res.status_code == 401
    assert "detail" in res.json()
    assert res.headers["X-Request-Id"].startswith("req_")
