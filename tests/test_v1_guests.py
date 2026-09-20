"""게스트 공개 진입, 쿠키 복원, 계정 간 격리, 서버 권한과 만료를 검증한다."""

import pytest
from app import db
from app.config import get_settings
from app.main import app
from app.v1 import guests
from app.v1.accounts import create_account
from app.v1.models import User
from app.v1.models_auth import GuestRateLimit
from app.v1.models_conversation import ChildProfile
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import auth, tick
from test_v1_conversations import ready_story

BASE = "/api/v1"


@pytest.fixture
def browser(monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_cookie_secure", True)
    monkeypatch.setattr(get_settings(), "debug_mode", True)
    monkeypatch.setattr(get_settings(), "ai_switch", False)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def enter(client):
    response = client.post(f"{BASE}/auth/guest", json={})
    assert response.status_code == 200, response.text
    token = response.json()
    headers = auth(token["accessToken"])
    me = client.get(f"{BASE}/me", headers=headers)
    assert me.status_code == 200, me.text
    return token, headers, me.json()


def test_distinct_browsers_have_separate_accounts_profiles_and_records(browser):
    first, a, me_a = enter(browser)
    with TestClient(app, base_url="https://testserver") as second_browser:
        second, b, me_b = enter(second_browser)
        assert first["user"]["id"] != second["user"]["id"]
        assert me_a["profile"]["id"] != me_b["profile"]["id"]
        # 같은 별명, 같은 멱등 키도 다른 계정의 활동을 재사용하지 않는다.
        created = []
        for client, headers in ((browser, a), (second_browser, b)):
            response = client.post(f"{BASE}/activity-sessions", json={"activityId": "path-teaching"},
                                   headers={**headers, "Idempotency-Key": "same-key"})
            assert response.status_code == 201, response.text
            created.append(response.json()["session"]["sessionId"])
        assert created[0] != created[1]
        for method, body in (("GET", None), ("PATCH", {"clientRevision": 1, "event": {"type": "TEXT", "value": "침입"}}),
                             ("DELETE", None)):
            response = second_browser.request(method, f"{BASE}/activity-sessions/{created[0]}", json=body, headers=b)
            assert response.status_code == 404, response.text
        foreign = second_browser.get(f"{BASE}/profiles/{me_a['profile']['id']}", headers=b)
        assert foreign.status_code == 404
        assert browser.get(f"{BASE}/activity-sessions/{created[0]}", headers=a).status_code == 200
        for client, headers, own in ((browser, a, created[0]), (second_browser, b, created[1])):
            listing = client.get(f"{BASE}/activity-sessions", headers=headers)
            assert listing.status_code == 200, listing.text
            assert own in listing.text
            assert (created[1] if own == created[0] else created[0]) not in listing.text
    with next(db.get_session()) as session:
        users = list(session.scalars(select(User)))
        assert len({u.family_id for u in users}) == 2
        assert len({u.child_id for u in users}) == 2
        assert all(u.role == "GUEST" and not u.is_tester for u in users)


def test_cookie_resume_rotation_and_logout(browser, frozen):
    first, headers, _ = enter(browser)
    assert first["user"]["role"] == "GUEST"
    assert first["user"]["needsFirstGreeting"] is False
    assert first["refreshExpiresIn"] == guests.TTL_SECONDS
    assert "refreshToken" not in first
    again, _, _ = enter(browser)
    assert again["user"]["id"] == first["user"]["id"]
    tick(frozen, 3600)
    refreshed = browser.post(f"{BASE}/auth/token/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["user"] == first["user"]
    assert refreshed.json()["refreshExpiresIn"] == guests.TTL_SECONDS - 3600
    cookie = refreshed.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    headers = auth(refreshed.json()["accessToken"])
    assert browser.post(f"{BASE}/auth/logout", headers=headers).status_code == 200
    assert browser.get(f"{BASE}/me", headers=headers).status_code == 401
    assert browser.post(f"{BASE}/auth/token/refresh").status_code == 401
    new, _, _ = enter(browser)
    assert new["user"]["id"] != first["user"]["id"]


def test_fixed_expiry_and_cleanup_preserve_other_accounts(browser, frozen):
    first, _, me = enter(browser)
    with next(db.get_session()) as session:
        regular = create_account(session, role="GUARDIAN")
        regular_id = regular.id
        session.commit()
    tick(frozen, guests.TTL_SECONDS - 60)
    refreshed = browser.post(f"{BASE}/auth/token/refresh").json()
    headers = auth(refreshed["accessToken"])
    assert refreshed["refreshExpiresIn"] == 60
    tick(frozen, 61)
    assert browser.get(f"{BASE}/me", headers=headers).status_code == 401
    assert browser.post(f"{BASE}/auth/token/refresh").status_code == 401
    new, _, _ = enter(browser)
    assert new["user"]["id"] != first["user"]["id"]
    with next(db.get_session()) as session:
        assert session.get(User, first["user"]["id"]) is None
        assert session.get(ChildProfile, me["profile"]["id"]) is None
        assert session.get(User, regular_id) is not None
        assert session.get(User, new["user"]["id"]) is not None


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "profiles"), ("POST", "guardian-links/invitations"),
    ("POST", "guardian-links/invitations/fake/accept"), ("POST", "stories/fake/share-requests"),
    ("PUT", "community/stories/fake/recommendation"), ("POST", "data-exports"),
    ("POST", "devices"), ("GET", "guardian/children"), ("POST", "debug/reset-account"),
])
def test_guest_cannot_use_account_or_sharing_features(browser, method, path):
    _, headers, _ = enter(browser)
    response = browser.request(method, f"{BASE}/{path}", json={}, headers=headers)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "GUEST_RESTRICTED"


def test_guest_write_budget_and_global_start_budget(browser, frozen):
    token, headers, _ = enter(browser)
    with next(db.get_session()) as session:
        window = frozen["now"].replace(hour=0, minute=0, second=0, microsecond=0)
        session.add(GuestRateLimit(key=f"write:{token['user']['id']}", window=window, count=300))
        session.commit()
    response = browser.post(f"{BASE}/activity-sessions", json={"activityId": "path-teaching"}, headers=headers)
    assert response.status_code == 429
    assert browser.get(f"{BASE}/home", headers=headers).status_code == 200
    assert browser.post(f"{BASE}/auth/logout", headers=headers).status_code == 200
    with next(db.get_session()) as session:
        row = session.get(GuestRateLimit, ("start:global", window))
        row.count = 1000
        session.commit()
    assert browser.post(f"{BASE}/auth/guest").status_code == 429


def test_guest_completes_story_and_other_guest_cannot_read_or_modify_it(browser, frozen):
    _, a, _ = enter(browser)
    conversation = ready_story(browser, {"headers": a}, frozen)
    result = browser.post(f"{BASE}/conversations/{conversation['id']}/complete", json={}, headers=a)
    assert result.status_code == 200, result.text
    story = result.json()["story"]
    own = browser.get(f"{BASE}/stories", headers=a)
    assert story["id"] in own.text
    with TestClient(app, base_url="https://testserver") as other:
        _, b, _ = enter(other)
        assert story["id"] not in other.get(f"{BASE}/stories", headers=b).text
        assert other.get(f"{BASE}/conversations/{conversation['id']}", headers=b).status_code == 404
        for method, body in (("GET", None), ("PATCH", {"title": "다른 제목", "version": 1}), ("DELETE", None)):
            response = other.request(method, f"{BASE}/stories/{story['id']}", headers=b, json=body)
            assert response.status_code == 404, response.text
    assert browser.get(f"{BASE}/stories/{story['id']}", headers=a).status_code == 200


def test_guest_entry_does_not_replace_an_existing_regular_account(browser, monkeypatch):
    monkeypatch.setattr(get_settings(), "auth_dev_login", True)
    signed_in = browser.post(f"{BASE}/auth/dev/login", json={"deviceKey": "regular-test-device"}).json()
    response = browser.post(f"{BASE}/auth/guest")
    assert response.status_code == 200
    assert response.json()["user"] == signed_in["user"]
    with next(db.get_session()) as session:
        assert not list(session.scalars(select(User).where(User.role == "GUEST")))
