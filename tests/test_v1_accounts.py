"""v1 계정·프로필·보호자 연결·동의 — 여러 아이 프로필, If-Match 수정, 보관 기간 고지, 초대·권한·해제,
동의 기록과 AI 관문, 소유권.
"""

from __future__ import annotations

import pytest
from app import auth as legacy_auth
from app import db
from app.config import get_settings
from app.main import app
from app.models import Child
from app.v1 import ai_gate, kakao
from app.v1.models_accounts import ProfileMember
from app.v1.models_auth import RefreshSession
from app.v1.models_conversation import ChildProfile
from app.v1.schemas_conversation import FirstGreetingLLM
from fastapi.testclient import TestClient
from sqlalchemy import select

from conftest import tick
from test_v1_auth import use_settings
from test_v1_conversations import make_user

PROFILES = "/api/v1/profiles"
CONSENTS = "/api/v1/consents"
INVITATIONS = "/api/v1/guardian-links/invitations"


def account(**kwargs) -> dict:
    return make_user(role="GUARDIAN", **kwargs)


def new_profile(client, user: dict, nickname: str = "하늘", **payload) -> dict:
    res = client.post(PROFILES, json={"nickname": nickname, **payload}, headers=user["headers"])
    assert res.status_code == 201, res.text
    return res.json()["profile"]


def grant_consent(client, user: dict, profile_id: str, document_id: str) -> dict:
    body = {"profileId": profile_id, "items": [{"documentId": document_id, "agreed": True}], "actor": "GUARDIAN"}
    res = client.post(CONSENTS, json=body, headers=user["headers"])
    assert res.status_code == 201, res.text
    return res.json()["items"][0]


def link_guardian(client, owner: dict, guardian: dict, profile_id: str, permissions: list[str]) -> dict:
    grant_consent(client, owner, profile_id, "privacy_child")
    invite = client.post(
        INVITATIONS, json={"profileId": profile_id, "permissions": permissions}, headers=owner["headers"]
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["invitation"]["token"]
    accepted = client.post(f"{INVITATIONS}/{token}/accept", headers=guardian["headers"])
    assert accepted.status_code == 200, accepted.text
    return accepted.json()["link"]


# --- 여러 아이 프로필 ---------------------------------------------------------------


def test_account_holds_several_profiles_each_with_its_own_child_row(client, frozen):
    user = account()
    first = new_profile(client, user, "하늘", gradeOrAgeBand="2학년", interests=["공룡"])
    second = new_profile(client, user, "바다", gradeOrAgeBand="4학년")
    assert first["isDefault"] is True and second["isDefault"] is False  # 첫 프로필이 기본
    assert first["role"] == "OWNER" and "MANAGE_DATA" in first["permissions"]
    assert first["needsFirstGreeting"] is True and first["version"] == 1

    listed = client.get(PROFILES, headers=user["headers"]).json()
    assert [p["id"] for p in listed["items"]] == [first["id"], second["id"]]
    assert listed["nextCursor"] is None
    page = client.get(f"{PROFILES}?limit=1", headers=user["headers"]).json()
    assert [p["id"] for p in page["items"]] == [first["id"]] and page["nextCursor"]
    rest = client.get(f"{PROFILES}?limit=1&cursor={page['nextCursor']}", headers=user["headers"]).json()
    assert [p["id"] for p in rest["items"]] == [second["id"]] and rest["nextCursor"] is None

    me = client.get("/api/v1/me", headers=user["headers"]).json()
    assert me["user"] == {"id": user["id"], "role": "GUARDIAN", "needsFirstGreeting": True}
    assert me["profile"] is None  # 첫인사를 아직 안 했다
    assert [p["id"] for p in me["profiles"]] == [first["id"], second["id"]]
    assert me["profiles"][0]["isDefault"] is True and me["profiles"][1]["nickname"] == "바다"

    with next(db.get_session()) as session:
        rows = list(session.scalars(select(ChildProfile)))
        assert {row.id for row in rows} == {first["id"], second["id"]}
        children = {row.child_id for row in rows}
        assert len(children) == 2  # 프로필마다 기존 엔진용 children 행이 따로 있다
        family_ids = {session.get(Child, child_id).family_id for child_id in children}
        assert len(family_ids) == 1  # 같은 가족 안에 있다


def test_profile_creation_replays_same_idempotency_key(client, frozen):
    user = account()
    headers = {**user["headers"], "Idempotency-Key": "prf-1"}
    first = client.post(PROFILES, json={"nickname": "하늘"}, headers=headers)
    again = client.post(PROFILES, json={"nickname": "하늘"}, headers=headers)
    assert first.status_code == 201 and again.json() == first.json()
    assert len(client.get(PROFILES, headers=user["headers"]).json()["items"]) == 1


def test_existing_account_gets_its_membership_created_on_first_read(client, frozen):
    """기존 계정 이어받기 — 첫인사로 만들어진 프로필만 있고 소속 행이 없어도 /me 가 채워 준다."""
    user = account()
    with next(db.get_session()) as session:
        session.add(ChildProfile(child_id=user["child_id"], user_id=user["id"], nickname="예전아이"))
        session.commit()
    me = client.get("/api/v1/me", headers=user["headers"]).json()
    assert len(me["profiles"]) == 1
    assert me["profiles"][0]["nickname"] == "예전아이" and me["profiles"][0]["isDefault"] is True
    assert me["profiles"][0]["role"] == "OWNER"
    with next(db.get_session()) as session:
        member = session.scalar(select(ProfileMember).where(ProfileMember.user_id == user["id"]))
        assert member is not None and member.child_id == user["child_id"]


# --- 수정과 If-Match ----------------------------------------------------------------


def test_voice_setting_defaults_on_and_persists_per_child(client, frozen):
    owner = account()
    first = new_profile(client, owner)
    second = new_profile(client, owner, "바다")
    path = f"{PROFILES}/{first['id']}/settings"
    assert client.get(path, headers=owner["headers"]).json()["settings"]["voiceEnabled"] is True
    with next(db.get_session()) as session:
        profile = session.get(ChildProfile, first["id"])
        session.get(Child, profile.child_id).permissions = {"browse_shared": True}
        session.commit()
    disabled = client.patch(path, json={"voiceEnabled": False, "version": 1}, headers=owner["headers"])
    assert disabled.status_code == 200 and disabled.json()["settings"]["voiceEnabled"] is False
    assert client.get(path, headers=owner["headers"]).json()["settings"]["voiceEnabled"] is False
    assert client.post("/api/v1/speech/stream-tickets", json={}, headers=owner["headers"]).status_code == 403
    with next(db.get_session()) as session:
        profile = session.get(ChildProfile, first["id"])
        assert session.get(Child, profile.child_id).permissions == {"voice": False, "browse_shared": True}
    other = client.get(f"{PROFILES}/{second['id']}/settings", headers=owner["headers"])
    assert other.json()["settings"]["voiceEnabled"] is True
    stale = client.patch(path, json={"voiceEnabled": True, "version": 1}, headers=owner["headers"])
    assert stale.status_code == 409
    unrelated = client.patch(path, json={"theme": "DARK"}, headers=owner["headers"])
    assert unrelated.json()["settings"]["voiceEnabled"] is False
    assert client.patch(path, json={"voiceEnabled": None}, headers=owner["headers"]).status_code == 400
    guardian = account()
    link_guardian(client, owner, guardian, first["id"], ["VIEW_PROFILE"])
    forbidden = client.patch(path, json={"voiceEnabled": True}, headers=guardian["headers"])
    assert forbidden.status_code == 403
    enabled = client.patch(path, json={"voiceEnabled": True}, headers=owner["headers"])
    assert enabled.json()["settings"]["voiceEnabled"] is True
    assert client.post("/api/v1/speech/stream-tickets", json={}, headers=owner["headers"]).status_code == 201


def test_profile_patch_checks_version_and_syncs_child_row(client, frozen):
    user = account()
    profile = new_profile(client, user, "하늘")
    res = client.patch(
        f"{PROFILES}/{profile['id']}",
        json={"nickname": "하늘이", "interests": ["공룡", "우주"]},
        headers={**user["headers"], "If-Match": '"1"'},
    )
    assert res.status_code == 200 and res.headers["ETag"] == '"2"'
    body = res.json()["profile"]
    assert body["nickname"] == "하늘이" and body["interests"] == ["공룡", "우주"] and body["version"] == 2

    stale = client.patch(
        f"{PROFILES}/{profile['id']}", json={"nickname": "다시"}, headers={**user["headers"], "If-Match": '"1"'}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["currentVersion"] == 2

    missing = client.patch(f"{PROFILES}/{profile['id']}", json={"nickname": "다시"}, headers=user["headers"])
    assert missing.status_code == 400 and missing.json()["error"]["details"]["fields"] == ["If-Match"]
    by_body = client.patch(
        f"{PROFILES}/{profile['id']}", json={"nickname": "하늘", "version": 2}, headers=user["headers"]
    )
    assert by_body.status_code == 200 and by_body.json()["profile"]["version"] == 3

    with next(db.get_session()) as session:
        row = session.scalar(select(ChildProfile).where(ChildProfile.id == profile["id"]))
        child = session.get(Child, row.child_id)
        assert child.nickname == "하늘" and child.likes == ["공룡", "우주"]  # 기존 엔진 개인화도 따라간다


def test_settings_patch_tells_what_gets_deleted(client, frozen):
    user = account()
    profile = new_profile(client, user, "하늘")
    current = client.get(f"{PROFILES}/{profile['id']}/settings", headers=user["headers"]).json()["settings"]
    assert current["ttsEnabled"] is True and current["guardianPreviewEnabled"] is True
    assert current["theme"] == "AUTO" and current["retentionDays"] == 90 and current["version"] == 1

    shorter = client.patch(
        f"{PROFILES}/{profile['id']}/settings",
        json={"retentionDays": 30, "theme": "DARK"},
        headers={**user["headers"], "If-Match": '"1"'},
    )
    assert shorter.status_code == 200
    body = shorter.json()
    assert body["settings"] == {
        "profileId": profile["id"], "voiceEnabled": True, "ttsEnabled": True, "guardianPreviewEnabled": True,
        "theme": "DARK", "retentionDays": 30, "version": 2, "updatedAt": "2026-09-14T01:00:00Z",
    }  # fmt: skip
    notice = body["retentionNotice"]
    assert notice["previousDays"] == 90 and notice["retentionDays"] == 30 and notice["deletesNow"] is True
    assert notice["effectiveAt"] == "2026-09-14T01:00:00Z" and notice["deletesBefore"] == "2026-08-15T01:00:00Z"
    assert notice["targets"] and "2026-08-15" in notice["message"]

    longer = client.patch(
        f"{PROFILES}/{profile['id']}/settings",
        json={"retentionDays": 365},
        headers={**user["headers"], "If-Match": '"2"'},
    )
    assert longer.json()["retentionNotice"]["deletesNow"] is False
    same = client.patch(
        f"{PROFILES}/{profile['id']}/settings",
        json={"ttsEnabled": False},
        headers={**user["headers"], "If-Match": '"3"'},
    )
    assert same.json()["retentionNotice"] is None and same.json()["settings"]["ttsEnabled"] is False
    conflict = client.patch(
        f"{PROFILES}/{profile['id']}/settings",
        json={"theme": "LIGHT"},
        headers={**user["headers"], "If-Match": '"1"'},
    )
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "VERSION_CONFLICT"
    # 명세 17절 예시에는 설정 PATCH 에 If-Match 가 없다 — 없으면 그대로 반영한다.
    plain = client.patch(f"{PROFILES}/{profile['id']}/settings", json={"theme": "LIGHT"}, headers=user["headers"])
    assert plain.status_code == 200 and plain.json()["settings"]["theme"] == "LIGHT"


# --- 보호자 초대 ---------------------------------------------------------------------


def test_invitation_needs_consent_is_single_use_and_expires(client, frozen):
    owner, guardian, stranger = account(), account(), account()
    profile = new_profile(client, owner, "하늘")

    blocked = client.post(INVITATIONS, json={"profileId": profile["id"]}, headers=owner["headers"])
    assert blocked.status_code == 403
    error = blocked.json()["error"]
    assert error["code"] == "CONSENT_REQUIRED" and error["details"]["documentIds"] == ["privacy_child"]

    grant_consent(client, owner, profile["id"], "privacy_child")
    invite = client.post(
        INVITATIONS,
        json={"profileId": profile["id"], "permissions": ["VIEW_PROFILE", "VIEW_STORIES"], "expiresInMinutes": 5},
        headers=owner["headers"],
    ).json()["invitation"]
    assert invite["token"].startswith("ginv_") and invite["expiresAt"] == "2026-09-14T01:05:00Z"

    accepted = client.post(f"{INVITATIONS}/{invite['token']}/accept", headers=guardian["headers"])
    assert accepted.status_code == 200
    link = accepted.json()["link"]
    assert link["role"] == "GUARDIAN" and link["permissions"] == ["VIEW_PROFILE", "VIEW_STORIES"]
    assert accepted.json()["profile"]["nickname"] == "하늘"

    used = client.post(f"{INVITATIONS}/{invite['token']}/accept", headers=stranger["headers"])
    assert used.status_code == 409 and used.json()["error"]["code"] == "INVITATION_ALREADY_USED"
    assert client.post(f"{INVITATIONS}/ginv_nope/accept", headers=stranger["headers"]).status_code == 404

    children = client.get("/api/v1/guardian/children", headers=guardian["headers"]).json()["items"]
    assert [c["profileId"] for c in children] == [profile["id"]]
    assert children[0]["linkId"] == link["id"] and children[0]["role"] == "GUARDIAN"
    links = client.get("/api/v1/guardian-links", headers=owner["headers"]).json()["items"]
    assert {row["role"] for row in links} == {"OWNER", "GUARDIAN"} and len(links) == 2

    later = client.post(
        INVITATIONS, json={"profileId": profile["id"], "expiresInMinutes": 5}, headers=owner["headers"]
    ).json()["invitation"]
    tick(frozen, 6 * 60)
    expired = client.post(f"{INVITATIONS}/{later['token']}/accept", headers=stranger["headers"])
    assert expired.status_code == 409 and expired.json()["error"]["code"] == "INVITATION_EXPIRED"


def test_link_permissions_are_enforced_and_unlink_keeps_data(client, frozen):
    owner, guardian = account(), account()
    profile = new_profile(client, owner, "하늘")
    link = link_guardian(client, owner, guardian, profile["id"], ["VIEW_STORIES"])

    denied = client.get(f"{PROFILES}/{profile['id']}", headers=guardian["headers"])
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
    assert denied.json()["error"]["details"]["required"] == "VIEW_PROFILE"

    changed = client.patch(
        f"/api/v1/guardian-links/{link['id']}",
        json={"permissions": ["VIEW_PROFILE", "VIEW_STORIES"]},
        headers=owner["headers"],
    )
    assert changed.status_code == 200
    assert client.get(f"{PROFILES}/{profile['id']}", headers=guardian["headers"]).status_code == 200
    guardian_patch = client.patch(
        f"{PROFILES}/{profile['id']}", json={"nickname": "내맘대로"}, headers={**guardian["headers"], "If-Match": "*"}
    )
    assert guardian_patch.status_code == 403  # MANAGE_DATA 가 없다

    owner_link = next(
        row
        for row in client.get("/api/v1/guardian-links", headers=owner["headers"]).json()["items"]
        if row["role"] == "OWNER"
    )  # noqa: E501
    assert (
        client.patch(
            f"/api/v1/guardian-links/{owner_link['id']}",
            json={"permissions": ["VIEW_PROFILE"]},
            headers=owner["headers"],
        ).status_code
        == 403
    )  # noqa: E501
    assert client.delete(f"/api/v1/guardian-links/{owner_link['id']}", headers=owner["headers"]).status_code == 403

    unlinked = client.delete(f"/api/v1/guardian-links/{link['id']}", headers=owner["headers"])
    assert unlinked.status_code == 200
    assert unlinked.json() == {
        "ok": True, "linkId": link["id"], "dataDeleted": False,
        "message": "연결만 해제했어요. 아이 기록은 그대로 남아 있어요.",
    }  # fmt: skip
    assert client.get(f"{PROFILES}/{profile['id']}", headers=guardian["headers"]).status_code == 404
    assert client.get("/api/v1/guardian/children", headers=guardian["headers"]).json()["items"] == []
    # 아이 기록은 그대로다.
    assert client.get(f"{PROFILES}/{profile['id']}", headers=owner["headers"]).json()["profile"]["nickname"] == "하늘"
    with next(db.get_session()) as session:
        assert session.scalar(select(ChildProfile).where(ChildProfile.id == profile["id"])) is not None


# --- 동의 ------------------------------------------------------------------------


def test_consents_are_versioned_and_actor_comes_from_the_login_account(client, frozen):
    owner, stranger = account(), account()
    profile = new_profile(client, owner, "하늘")

    documents = client.get("/api/v1/legal-documents?locale=ko-KR", headers=owner["headers"]).json()["items"]
    assert [doc["id"] for doc in documents] == [
        "privacy_child", "ai_conversation", "voice_retention", "community_share",
    ]  # fmt: skip
    assert all(
        doc["draft"] is True and doc["locale"] == "ko-KR" and doc["version"] == "2026-09-18" for doc in documents
    )
    assert "법률 검토 전 초안" in documents[0]["draftNotice"]
    assert client.get("/api/v1/legal-documents?locale=en-US", headers=owner["headers"]).status_code == 400

    created = client.post(
        CONSENTS,
        json={
            "profileId": profile["id"],
            "items": [
                {"documentId": "ai_conversation", "version": "2026-09-18", "agreed": True},
                {"documentId": "community_share", "agreed": False},  # 동의하지 않음 — 기록을 만들지 않는다
            ],
            "actor": "CHILD",  # 서버는 본문의 actor 를 믿지 않는다
        },
        headers=owner["headers"],
    )
    assert created.status_code == 201
    assert len(created.json()["items"]) == 1
    consent = created.json()["items"][0]
    assert consent["actor"] == {"userId": owner["id"], "role": "GUARDIAN"}
    assert consent["documentVersion"] == "2026-09-18" and consent["status"] == "GRANTED"
    assert consent["current"] is True and consent["profileId"] == profile["id"]

    again = grant_consent(client, owner, profile["id"], "ai_conversation")
    assert again["id"] == consent["id"]  # 같은 버전에 다시 동의해도 기록을 새로 만들지 않는다
    unknown = client.post(
        CONSENTS, json={"profileId": profile["id"], "items": [{"documentId": "nope"}]}, headers=owner["headers"]
    )
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    stale = client.post(
        CONSENTS,
        json={"profileId": profile["id"], "items": [{"documentId": "voice_retention", "version": "2020-01-01"}]},
        headers=owner["headers"],
    )
    assert stale.status_code == 409 and stale.json()["error"]["details"]["currentVersion"] == "2026-09-18"

    listed = client.get(f"{CONSENTS}?profileId={profile['id']}", headers=owner["headers"]).json()
    assert [row["id"] for row in listed["items"]] == [consent["id"]] and listed["nextCursor"] is None

    by_body = client.post(
        CONSENTS,
        json={"profileId": profile["id"], "items": [{"documentId": "ai_conversation", "agreed": False}]},
        headers=owner["headers"],
    )
    assert by_body.json()["items"][0]["status"] == "REVOKED"  # agreed: false 는 철회다
    consent = grant_consent(client, owner, profile["id"], "ai_conversation")

    revoked = client.delete(f"{CONSENTS}/{consent['id']}", headers=owner["headers"])
    assert revoked.status_code == 200
    assert revoked.json()["consent"]["status"] == "REVOKED" and revoked.json()["consent"]["current"] is False
    assert revoked.json()["consent"]["revokedAt"] == "2026-09-14T01:00:00Z"
    assert client.delete(f"{CONSENTS}/{consent['id']}", headers=stranger["headers"]).status_code == 404


# --- AI 관문 ----------------------------------------------------------------------


def _with_openai_key(monkeypatch) -> None:
    """실제 키 없이 demo 모드 판정만 살린다(OpenAI 호출은 전부 가짜)."""
    settings = get_settings().model_copy(update={"openai_api_key": "test-key", "child_data_mode": "demo"})
    monkeypatch.setattr(legacy_auth, "get_settings", lambda: settings)


def test_ai_gate_follows_guardian_consent_without_fabricating_dialogue(client, frozen, monkeypatch):
    _with_openai_key(monkeypatch)
    monkeypatch.setattr(ai_gate, "moderate", lambda *_: None)
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return FirstGreetingLLM(
            message="안녕! 어떤 이야기를 나누고 싶어?",
            changes=[],
            context_summary="",
            propose_review=False,
            profile_summary=None,
            end_intent="none",
        )

    monkeypatch.setattr(ai_gate, "call_structured", fake)
    user = account()
    profile = new_profile(client, user, "별", makeDefault=True)
    before = client.post("/api/v1/first-greeting/sessions", headers=user["headers"])
    assert before.status_code == 503 and calls == []
    assert before.json()["error"]["details"]["reason"] == "child_data_mode_off"

    consent = grant_consent(client, user, profile["id"], "ai_conversation")
    start = client.post("/api/v1/first-greeting/sessions", headers=user["headers"])
    assert start.status_code == 200
    sid = start.json()["sessionId"]
    after = client.post(
        f"/api/v1/first-greeting/sessions/{sid}/messages",
        json={"clientMessageId": "c2", "input": {"type": "TEXT", "text": "공룡을 좋아해"}},
        headers=user["headers"],
    )
    assert after.status_code == 200 and after.json()["assistantMessage"]["source"] == "ai"
    assert len(calls) == 2
    client.delete(f"{CONSENTS}/{consent['id']}", headers=user["headers"])
    revoked = client.post(
        f"/api/v1/first-greeting/sessions/{sid}/messages",
        json={"clientMessageId": "c3", "input": {"type": "TEXT", "text": "티라노사우루스 이빨이 커서 좋아"}},
        headers=user["headers"],
    )
    assert revoked.status_code == 503 and len(calls) == 2
    detail = client.get(f"/api/v1/first-greeting/sessions/{sid}", headers=user["headers"]).json()
    assert len(detail["messages"]) == 3


def test_ai_gate_keeps_blocking_when_there_is_no_api_key(client, frozen, monkeypatch):
    """키가 없거나 AI 스위치가 꺼져 있으면 동의가 있어도 AI 를 쓰지 않는다."""
    settings = get_settings().model_copy(update={"openai_api_key": ""})
    monkeypatch.setattr(legacy_auth, "get_settings", lambda: settings)
    user = account()
    profile = new_profile(client, user, "별", makeDefault=True)
    grant_consent(client, user, profile["id"], "ai_conversation")
    with next(db.get_session()) as session:
        child = session.get(Child, session.get(ChildProfile, profile["id"]).child_id)
        assert ai_gate.block_reason(child) == "no_api_key"


# --- 모바일 로그인 멱등 --------------------------------------------------------------


@pytest.fixture
def kakao_client(monkeypatch):
    """카카오 HTTP 는 mock. 모바일 로그인만 쓴다."""
    monkeypatch.setattr(kakao, "get_user_id", lambda token: "1001")
    monkeypatch.setattr(kakao, "get_token_info", lambda token: kakao.TokenInfo(user_id="1001", app_id="777"))
    return TestClient(app)


def test_mobile_login_with_idempotency_key_reuses_the_refresh_chain(monkeypatch, kakao_client):
    use_settings(monkeypatch)
    web = kakao_client
    body = {"platform": "ANDROID", "kakaoAccessToken": "kakao-token-1"}
    headers = {"Idempotency-Key": "boot-1"}
    first = web.post("/api/v1/auth/kakao/mobile", json=body, headers=headers)
    again = web.post("/api/v1/auth/kakao/mobile", json=body, headers=headers)
    assert first.status_code == 200 and again.status_code == 200
    assert "Idempotent-Replayed" not in first.headers
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["user"]["id"] == first.json()["user"]["id"]
    # 토큰은 다시 발급하되 refresh 계열(세션)은 새로 만들지 않는다.
    assert again.json()["accessToken"] != first.json()["accessToken"]
    with next(db.get_session()) as session:
        assert len({row.chain_id for row in session.scalars(select(RefreshSession))}) == 1
    web.post("/api/v1/auth/kakao/mobile", json=body)
    with next(db.get_session()) as session:
        assert len({row.chain_id for row in session.scalars(select(RefreshSession))}) == 2


# --- 소유권 ------------------------------------------------------------------------


def test_other_accounts_cannot_see_the_profile(client, frozen):
    owner, stranger = account(), account()
    profile = new_profile(client, owner, "하늘")
    headers = stranger["headers"]
    assert client.get(f"{PROFILES}/{profile['id']}", headers=headers).status_code == 404
    assert client.get(f"{PROFILES}/{profile['id']}/settings", headers=headers).status_code == 404
    patched = client.patch(f"{PROFILES}/{profile['id']}", json={"nickname": "x"}, headers={**headers, "If-Match": "*"})
    assert patched.status_code == 404 and patched.json()["error"]["code"] == "PROFILE_NOT_FOUND"
    assert client.get(f"{CONSENTS}?profileId={profile['id']}", headers=headers).status_code == 404
    assert client.post(INVITATIONS, json={"profileId": profile["id"]}, headers=headers).status_code == 404
    assert client.get(PROFILES, headers=headers).json()["items"] == []
    assert client.get(f"{PROFILES}/{profile['id']}", headers=owner["headers"]).status_code == 200
