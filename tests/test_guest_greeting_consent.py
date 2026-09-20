"""게스트 보호자 확인, AI 관문, 철회 및 프로필 격리 계약."""

import pytest
from app import db
from app.main import app
from app.v1 import ai_gate, guests
from app.v1.models import User
from app.v1.models_accounts import DOCUMENT_BY_ID, Consent
from fastapi.testclient import TestClient
from sqlalchemy import select

import test_v1_first_greeting
import test_v1_guests
from test_v1_conversations import make_user
from test_v1_first_greeting import begin, filled, reply
from test_v1_guests import enter

ai = test_v1_first_greeting.ai
browser = test_v1_guests.browser

BASE = "/api/v1"
GREETING = f"{BASE}/first-greeting/sessions"


@pytest.mark.parametrize("role", ["GUEST", "GUARDIAN"])
def test_readiness_requires_consent_and_respects_ai_configuration_without_calling_ai(browser, ai, monkeypatch, role):
    if role == "GUEST":
        _, headers, me = enter(browser)
    else:
        headers = make_user(role=role)["headers"]
        me = browser.get(f"{BASE}/me", headers=headers).json()
    monkeypatch.setattr(ai_gate, "ai_block_reason", lambda _: "child_data_mode_off")
    path = f"{BASE}/first-greeting/readiness"
    assert browser.get(path, headers=headers).json()["available"] is False
    profile_id = next(profile["id"] for profile in me["profiles"] if profile["isDefault"])
    assert grant(browser, headers, profile_id).status_code == 201
    assert browser.get(path, headers=headers).json() == {"available": True, "reason": None, "message": None}
    for reason in ("ai_disabled", "no_api_key"):
        monkeypatch.setattr(ai_gate, "ai_block_reason", lambda _, reason=reason: reason)
        response = browser.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json()["available"] is False and response.json()["reason"] == reason
        assert response.json()["message"]
    assert not ai[1]


def grant(client, headers, profile, *, confirmed=True, documents=guests.CONSENT_DOCUMENTS):
    return client.post(f"{BASE}/consents", headers=headers, json={
        "profileId": profile, "guardianConfirmed": confirmed,
        "items": [{"documentId": doc, "version": DOCUMENT_BY_ID[doc].version, "agreed": True}
                  for doc in documents],
    })


def test_new_regular_account_can_consent_before_greeting_and_keeps_same_profile(browser, frozen, ai):
    user = make_user(role="GUARDIAN")
    headers = user["headers"]
    me = browser.get(f"{BASE}/me", headers=headers).json()
    assert me["user"]["needsFirstGreeting"] is True and me["profile"] is None
    assert len(me["profiles"]) == 1
    profile = me["profiles"][0]
    assert profile["isDefault"] and profile["needsFirstGreeting"] and not profile["nickname"]
    assert browser.get(f"{BASE}/me", headers=headers).json() == me
    assert grant(browser, headers, profile["id"]).status_code == 201
    start = begin(browser, user)
    review = filled(browser, user, start["sessionId"], ai)
    done = browser.post(f"{GREETING}/{start['sessionId']}/complete", headers=headers,
                        json={"profileRevision": review["profileRevision"]})
    assert done.status_code == 200, done.text
    completed = browser.get(f"{BASE}/me", headers=headers).json()
    assert not completed["user"]["needsFirstGreeting"]
    assert completed["profile"]["id"] == profile["id"]
    assert len(completed["profiles"]) == 1
    assert len(browser.get(f"{BASE}/consents?profileId={profile['id']}&currentOnly=true", headers=headers).json()["items"]) == 2


@pytest.mark.parametrize("role", ["GUEST", "GUARDIAN"])
def test_home_and_community_load_after_first_greeting(browser, frozen, ai, role):
    if role == "GUEST":
        _, headers, me = enter(browser)
    else:
        headers = make_user(role=role)["headers"]
        me = browser.get(f"{BASE}/me", headers=headers).json()
    profile_id = next(profile["id"] for profile in me["profiles"] if profile["isDefault"])
    assert grant(browser, headers, profile_id).status_code == 201
    user = {"headers": headers}
    start = begin(browser, user)
    review = filled(browser, user, start["sessionId"], ai)
    done = browser.post(
        f"{GREETING}/{start['sessionId']}/complete", headers=headers,
        json={"profileRevision": review["profileRevision"]},
    )
    assert done.status_code == 200, done.text
    home = browser.get(f"{BASE}/home", headers=headers)
    assert home.status_code == 200, home.text
    assert home.json()["profile"] == {"nickname": "별", "needsFirstGreeting": False}
    assert home.json()["recommendations"]
    assert home.json()["communityStories"] == []
    community = browser.get(f"{BASE}/community/stories", headers=headers)
    assert community.status_code == 200, community.text
    assert community.json()["items"] == []


def test_requires_guardian_confirmation_current_versions_and_own_profile(browser, ai):
    token, headers, me = enter(browser)
    profile = me["profile"]["id"]
    blocked = browser.post(GREETING, headers=headers)
    assert blocked.status_code == 503, blocked.text
    assert blocked.json()["error"]["details"]["reason"] == "guest_consent_required"
    assert not ai[1]
    assert grant(browser, headers, profile, confirmed=False).status_code == 403
    assert grant(browser, headers, profile, documents=("voice_retention",)).status_code == 403
    for version, status in ((None, 400), ("old-version", 409)):
        response = browser.post(f"{BASE}/consents", headers=headers, json={
            "profileId": profile, "guardianConfirmed": True,
            "items": [{"documentId": "privacy_child", "version": version, "agreed": True}],
        })
        assert response.status_code == status, response.text
    with TestClient(app, base_url="https://testserver") as other:
        _, other_headers, other_me = enter(other)
        assert grant(browser, headers, other_me["profile"]["id"]).status_code == 404
        granted = grant(browser, headers, profile)
        assert granted.status_code == 201, granted.text
        assert grant(browser, headers, profile).json() == granted.json()
        assert other.get(f"{BASE}/consents?profileId={profile}", headers=other_headers).status_code == 404
        consent_id = granted.json()["items"][0]["id"]
        assert other.delete(f"{BASE}/consents/{consent_id}", headers=other_headers).status_code == 404
        assert other.post(GREETING, headers=other_headers).status_code == 503
    with next(db.get_session()) as session:
        assert len(list(session.scalars(select(Consent)))) == 2
        user = session.get(User, token["user"]["id"])
        assert user.role == "GUEST" and not user.is_tester


def test_guest_can_finish_greeting_without_changing_another_guest(browser, frozen, ai):
    _, headers, me = enter(browser)
    with TestClient(app, base_url="https://testserver") as other:
        _, other_headers, other_me = enter(other)
        assert grant(browser, headers, me["profile"]["id"]).status_code == 201
        user = {"headers": headers}
        start = begin(browser, user)
        assert start["messages"][0]["source"] == "ai"
        assert begin(browser, user)["sessionId"] == start["sessionId"]
        assert other.get(f"{GREETING}/{start['sessionId']}", headers=other_headers).status_code == 404
        review = filled(browser, user, start["sessionId"], ai)
        done = browser.post(f"{GREETING}/{start['sessionId']}/complete", headers=headers,
                            json={"profileRevision": review["profileRevision"]})
        assert done.status_code == 200, done.text
        updated = browser.get(f"{BASE}/me", headers=headers).json()
        assert updated["profile"]["id"] == me["profile"]["id"]
        assert updated["profile"]["nickname"] == "별"
        assert other.get(f"{BASE}/me", headers=other_headers).json()["profile"] == other_me["profile"]


@pytest.mark.parametrize("document", guests.CONSENT_DOCUMENTS)
def test_each_consent_is_required_and_revocation_stops_new_ai_calls(browser, frozen, ai, document):
    _, headers, me = enter(browser)
    profile = me["profile"]["id"]
    remaining = tuple(doc for doc in guests.CONSENT_DOCUMENTS if doc != document)
    assert grant(browser, headers, profile, documents=remaining).status_code == 201
    assert browser.post(GREETING, headers=headers).status_code == 503
    granted = grant(browser, headers, profile).json()["items"]
    user = {"headers": headers}
    start = begin(browser, user)
    consent = next(item for item in granted if item["documentId"] == document)
    assert browser.delete(f"{BASE}/consents/{consent['id']}", headers=headers).status_code == 200
    active = browser.get(f"{BASE}/consents?profileId={profile}&currentOnly=true", headers=headers).json()
    assert all(item["documentId"] != document for item in active["items"])
    blocked = reply(browser, user, start["sessionId"], "공룡이 좋아")
    assert blocked.status_code == 503, blocked.text
    assert blocked.json()["error"]["details"]["reason"] == "guest_consent_required"
    assert len(ai[1]) == 1
    assert browser.get(f"{BASE}/home", headers=headers).status_code == 200
    assert browser.post(f"{BASE}/activity-sessions", headers=headers,
                        json={"activityId": "path-teaching"}).status_code == 201
