"""가족·아이 토큰과 권한, ZDR 전 아동 데이터 차단, 친구 모임."""

from __future__ import annotations

from app.auth import ai_block_reason
from app.config import get_settings
from app.models import Child

from conftest import auth, grant, make_family


def test_guardian_and_child_tokens_are_scoped(client, family, child):
    other = make_family(client)
    assert client.get(f"/guardian/children/{child['id']}", headers=other["headers"]).status_code == 404
    assert client.get("/guardian/children", headers=child["headers"]).status_code == 401
    assert client.get("/me", headers=family["headers"]).status_code == 401
    me = client.get("/me", headers=child["headers"]).json()
    assert me["nickname"] == "하늘"
    assert me["permissions"] == {"voice": False, "browseShared": False, "publishRequest": False}
    grant(client, child, voice=True)
    assert client.get("/me", headers=child["headers"]).json()["permissions"]["voice"] is True
    client.delete(f"/guardian/children/{child['id']}/devices", headers=family["headers"])
    assert client.get("/me", headers=child["headers"]).status_code == 401


def test_real_child_data_needs_zdr_mode_but_tester_accounts_do_not(monkeypatch):
    settings = get_settings().model_copy(update={"openai_api_key": "sk-test", "child_data_mode": "demo"})
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    assert ai_block_reason(Child(is_tester=False)) == "child_data_mode_off"
    assert ai_block_reason(Child(is_tester=True)) is None
    monkeypatch.setattr("app.auth.get_settings", lambda: settings.model_copy(update={"child_data_mode": "child"}))
    assert ai_block_reason(Child(is_tester=False)) is None


def test_token_header_format(client):
    assert client.get("/me", headers=auth("gt_wrong")).status_code == 401
    assert client.get("/me").status_code == 401


def test_circle_invite_code_joins_other_family(client, family):
    circle = client.post("/guardian/circles", json={"name": "우리반 친구들"}, headers=family["headers"]).json()
    other = make_family(client)
    joined = client.post("/guardian/circles/join", json={"code": circle["code"].lower()}, headers=other["headers"])
    assert joined.json()["id"] == circle["id"]
    assert [c["id"] for c in client.get("/guardian/circles", headers=other["headers"]).json()] == [circle["id"]]
    assert client.post("/guardian/circles/join", json={"code": "ZZZZ9999"}, headers=other["headers"]).status_code == 404
