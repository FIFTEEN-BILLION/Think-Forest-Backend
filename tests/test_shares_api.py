"""모험 이야기 공유 — 권한, 보호자 승인, 공개 범위, 신고, 보호자 모험으로 대화 시작."""

from __future__ import annotations

from conftest import grant, make_child, make_family, talk_to_story


def test_sharing_needs_permission_guardian_approval_and_hides_after_reports(client, family, child, frozen):
    story = talk_to_story(client, child, frozen)["final"]["story"]
    request = {"kind": "story", "refId": story["id"], "visibility": "family"}
    assert client.post("/children/me/shares", json=request, headers=child["headers"]).status_code == 403
    grant(client, child, publishRequest=True, browseShared=True)

    item = client.post("/children/me/shares", json=request, headers=child["headers"]).json()
    assert item["status"] == "pending_guardian" and item["authorLabel"] == "하늘"
    assert client.get("/shares", headers=child["headers"]).json() == []
    approved = client.post(
        f"/guardian/shares/{item['id']}/decision", json={"approve": True}, headers=family["headers"]
    ).json()
    assert approved["status"] == "published"
    assert [s["id"] for s in client.get("/shares", headers=child["headers"]).json()] == [item["id"]]

    outsider = make_family(client)
    assert client.get(f"/shares/{item['id']}", headers=outsider["headers"]).status_code == 404

    community = client.post(
        "/children/me/shares", json={**request, "visibility": "community"}, headers=child["headers"]
    ).json()
    decided = client.post(
        f"/guardian/shares/{community['id']}/decision", json={"approve": True}, headers=family["headers"]
    ).json()
    assert decided["status"] == "pending_review"  # ZDR 전 아동 콘텐츠는 외부 검사로 보내지 않고 사람 검토 대기

    reporters = [family, *(make_family(client) for _ in range(2))]
    circle = client.post("/guardian/circles", json={"name": "모임"}, headers=family["headers"]).json()
    circle_item = client.post(
        "/guardian/adventures",
        json={"category": "math", "title": "피자 모험", "hook": "피자를 어떻게 나눌까?", "visibility": "circle", "circleId": circle["id"]},
        headers=family["headers"],
    ).json()
    for reporter in reporters[1:]:
        client.post("/guardian/circles/join", json={"code": circle["code"]}, headers=reporter["headers"])
    statuses = [
        client.post(f"/shares/{circle_item['id']}/reports", headers=r["headers"]).json()["status"] for r in reporters
    ]
    assert statuses == ["published", "published", "hidden"]


def test_unsafe_guardian_adventure_is_rejected(client, family):
    r = client.post(
        "/guardian/adventures",
        json={"category": "history", "title": "선거 이야기", "hook": "대통령은 누가 좋을까?", "visibility": "family"},
        headers=family["headers"],
    )
    assert r.status_code == 422


def test_guardian_adventure_can_open_a_talk_for_other_families(client, family, child, frozen):
    circle = client.post("/guardian/circles", json={"name": "우리반 친구들"}, headers=family["headers"]).json()
    friend_family = make_family(client)
    client.post("/guardian/circles/join", json={"code": circle["code"]}, headers=friend_family["headers"])
    friend = make_child(client, friend_family, nickname="바다")
    adventure = client.post(
        "/guardian/adventures",
        json={
            "category": "science", "title": "그림자 놀이", "hook": "손으로 어떤 그림자를 만들 수 있을까?",
            "followUps": ["그림자를 어디에 쓸 수 있을까?"], "visibility": "circle", "circleId": circle["id"],
        },
        headers=family["headers"],
    ).json()  # fmt: skip
    assert adventure["status"] == "published"
    blocked = client.post("/talks", json={"sharedItemId": adventure["id"]}, headers=friend["headers"])
    assert blocked.status_code == 403
    grant(client, friend, browseShared=True)
    talk = client.post("/talks", json={"sharedItemId": adventure["id"]}, headers=friend["headers"]).json()
    assert talk["topic"]["title"] == "그림자 놀이" and "손으로 어떤 그림자" in talk["turns"][0]["text"]
