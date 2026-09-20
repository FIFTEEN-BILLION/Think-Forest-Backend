"""v1 주제·홈·/me·나의 책장 — 목록·상세·생성·안전, 홈 조합, 책장 목록·상세·즐겨찾기, 소유권."""

from __future__ import annotations

from app import db
from app.models import SafetyEvent
from app.v1.models_conversation import UserTopic
from sqlalchemy import select

from conftest import tick
from test_v1_conversations import make_user, ready_story, say, start

TOPICS = "/api/v1/topics"


# --- 주제 ------------------------------------------------------------------------


def test_topic_list_filters_and_cursor(client, frozen):
    user = make_user()
    everything = client.get(TOPICS, headers=user["headers"]).json()
    assert len(everything["items"]) == 10 and everything["nextCursor"] is None
    first = everything["items"][0]
    assert first == {
        "id": "topic_ice_cup", "title": "얼음물 컵의 물방울", "category": "SCIENCE", "source": "BANK",
        "hook": "얼음물을 컵에 담아 두면 컵 바깥에 물방울이 생겨. 그 물은 어디에서 왔을까?", "estimatedMinutes": 10,
    }  # fmt: skip
    science = client.get(f"{TOPICS}?category=SCIENCE", headers=user["headers"]).json()
    assert [t["id"] for t in science["items"]] == ["topic_ice_cup", "topic_airplane", "topic_snow"]
    assert [t["id"] for t in client.get(f"{TOPICS}?query=피자", headers=user["headers"]).json()["items"]] == [
        "topic_pizza_share"
    ]
    page1 = client.get(f"{TOPICS}?limit=4", headers=user["headers"]).json()
    page2 = client.get(f"{TOPICS}?limit=4&cursor={page1['nextCursor']}", headers=user["headers"]).json()
    page3 = client.get(f"{TOPICS}?limit=4&cursor={page2['nextCursor']}", headers=user["headers"]).json()
    ids = [t["id"] for p in (page1, page2, page3) for t in p["items"]]
    assert ids == [t["id"] for t in everything["items"]] and page3["nextCursor"] is None
    recommended = client.get(f"{TOPICS}?recommended=true", headers=user["headers"]).json()["items"]
    assert len(recommended) == 3 and "topic_ice_cup" in {t["id"] for t in recommended}
    assert client.get(f"{TOPICS}?cursor=zzz", headers=user["headers"]).status_code == 400
    assert client.get(f"{TOPICS}?category=SPORTS", headers=user["headers"]).json()["error"]["code"] == "INVALID_INPUT"


def test_topic_detail_create_and_unsafe_topic(client, frozen):
    owner, other = make_user(), make_user()
    detail = client.get(f"{TOPICS}/topic_snow", headers=owner["headers"]).json()["topic"]
    assert detail["title"] == "하얀 눈" and len(detail["questions"]) == 4
    assert client.get(f"{TOPICS}/topic_nope", headers=owner["headers"]).json()["error"]["code"] == "TOPIC_NOT_FOUND"

    headers = {**owner["headers"], "Idempotency-Key": "topic-1"}
    created = client.post(TOPICS, json={"title": "무지개는 왜 여러 색으로 보일까?", "category": "SCIENCE"}, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["topic"]["id"].startswith("topic_user_") and body["topic"]["source"] == "USER"
    assert body["safety"] == {"allowed": True, "reason": None}
    assert client.post(TOPICS, json={"title": "무지개는 왜 여러 색으로 보일까?"}, headers=headers).json() == body

    mine = client.get(f"{TOPICS}?category=SCIENCE", headers=owner["headers"]).json()["items"]
    assert mine[-1]["id"] == body["topic"]["id"] and mine[-1]["hook"] == "무지개는 왜 여러 색으로 보일까? 네 생각은 어때?"
    assert body["topic"]["id"] not in {t["id"] for t in client.get(TOPICS, headers=other["headers"]).json()["items"]}
    assert client.get(f"{TOPICS}/{body['topic']['id']}", headers=other["headers"]).status_code == 404
    assert client.post("/api/v1/conversations", json={"topicId": body["topic"]["id"]}, headers=other["headers"]).status_code == 404  # noqa: E501
    conversation = client.post("/api/v1/conversations", json={"topicId": body["topic"]["id"]}, headers=owner["headers"])
    assert conversation.status_code == 201 and conversation.json()["topic"]["category"] == "SCIENCE"

    unsafe = client.post(TOPICS, json={"title": "야한 이야기", "category": "FEELINGS"}, headers=owner["headers"])
    assert unsafe.status_code == 422 and unsafe.json()["error"]["code"] == "UNSAFE_TOPIC"
    too_long = client.post(TOPICS, json={"title": "가" * 41}, headers=owner["headers"])
    assert too_long.status_code == 400
    with next(db.get_session()) as session:
        assert [t.title for t in session.scalars(select(UserTopic)).all()] == ["무지개는 왜 여러 색으로 보일까?"]
        assert [e.category for e in session.scalars(select(SafetyEvent)).all()] == ["sexual"]


# --- 홈 · /me -------------------------------------------------------------------


def test_home_and_me_for_new_user_then_with_activity(client, frozen):
    user = make_user()
    me = client.get("/api/v1/me", headers=user["headers"]).json()
    assert me["user"] == {"id": user["id"], "role": "CHILD", "needsFirstGreeting": True}
    assert me["profile"] is None
    assert len(me["profiles"]) == 1
    pending = me["profiles"][0]
    assert pending["isDefault"] and pending["needsFirstGreeting"]
    assert pending["nickname"] == ""
    assert "MANAGE_DATA" in pending["permissions"]
    assert client.get("/api/v1/me", headers=user["headers"]).json()["profiles"] == me["profiles"]

    home = client.get("/api/v1/home", headers=user["headers"]).json()
    assert home["profile"] == {"nickname": None, "needsFirstGreeting": True}
    assert len(home["recommendations"]) == 3
    assert home["recommendations"][0] == {
        "topicId": "topic_ice_cup", "title": "얼음물 컵의 물방울", "category": "SCIENCE",
        "reason": "오늘은 과학 탐험의 날이에요.", "estimatedMinutes": 10,
    }  # fmt: skip
    assert all(r["reason"] for r in home["recommendations"])
    assert home["resume"] is None and home["recentWords"] == [] and home["communityStories"] == []
    assert home["weeklyActivity"] == {"conversationDays": 0, "completedStories": 0}

    conversation = start(client, user)
    tick(frozen, 30)
    say(client, user, conversation, option="SEEN")
    home = client.get("/api/v1/home", headers=user["headers"]).json()
    assert home["resume"]["conversationId"] == conversation["id"]
    assert home["resume"]["title"] == "얼음물 컵의 물방울" and home["resume"]["status"] == "ACTIVE"
    assert home["weeklyActivity"] == {"conversationDays": 1, "completedStories": 0}
    assert "topic_ice_cup" not in {r["topicId"] for r in home["recommendations"]}  # 최근 대화한 주제는 미룬다
    assert client.get("/api/v1/home").status_code == 401


# --- 나의 책장 --------------------------------------------------------------------


def _complete(client, user, frozen, topic_id: str) -> dict:
    conversation = ready_story(client, user, frozen, topic_id)
    res = client.post(f"/api/v1/conversations/{conversation['id']}/complete", json={}, headers=user["headers"])
    assert res.status_code == 200, res.text
    return res.json()["story"]


def test_stories_list_detail_favorite_and_ownership(client, frozen):
    owner, other = make_user(nickname="별"), make_user()
    snow = _complete(client, owner, frozen, "topic_snow")
    tick(frozen, 60)
    ice = _complete(client, owner, frozen, "topic_ice_cup")

    listed = client.get("/api/v1/stories", headers=owner["headers"]).json()
    assert [s["id"] for s in listed["items"]] == [ice["id"], snow["id"]] and listed["nextCursor"] is None
    assert set(listed["items"][0]) == {
        "id", "title", "summary", "category", "favorite", "version", "sourceConversationId", "createdAt", "updatedAt"
    }  # fmt: skip
    page1 = client.get("/api/v1/stories?limit=1", headers=owner["headers"]).json()
    page2 = client.get(f"/api/v1/stories?limit=1&cursor={page1['nextCursor']}", headers=owner["headers"]).json()
    assert [page1["items"][0]["id"], page2["items"][0]["id"]] == [ice["id"], snow["id"]]
    assert [s["id"] for s in client.get("/api/v1/stories?query=하얀 눈", headers=owner["headers"]).json()["items"]] == [
        snow["id"]
    ]
    assert client.get("/api/v1/stories?from=2026-09-15", headers=owner["headers"]).json()["items"] == []
    assert len(client.get("/api/v1/stories?from=2026-09-14&to=2026-09-14", headers=owner["headers"]).json()["items"]) == 2

    detail = client.get(f"/api/v1/stories/{ice['id']}", headers=owner["headers"]).json()["story"]
    assert detail == ice
    assert detail["topic"] == {"id": "topic_ice_cup", "title": "얼음물 컵의 물방울", "category": "SCIENCE"}

    fav = client.put(f"/api/v1/stories/{snow['id']}/favorite", headers=owner["headers"]).json()["story"]
    assert fav["favorite"] is True and fav["version"] == 1
    assert client.put(f"/api/v1/stories/{snow['id']}/favorite", headers=owner["headers"]).json()["story"] == fav
    favorites = client.get("/api/v1/stories?favorite=true", headers=owner["headers"]).json()["items"]
    assert [s["id"] for s in favorites] == [snow["id"]]
    unfav = client.delete(f"/api/v1/stories/{snow['id']}/favorite", headers=owner["headers"]).json()["story"]
    assert unfav["favorite"] is False
    assert client.get("/api/v1/stories?favorite=true", headers=owner["headers"]).json()["items"] == []

    assert client.get("/api/v1/stories", headers=other["headers"]).json()["items"] == []
    for method in ("GET", "PUT", "DELETE"):
        path = f"/api/v1/stories/{ice['id']}" + ("" if method == "GET" else "/favorite")
        res = client.request(method, path, headers=other["headers"])
        assert res.status_code == 404 and res.json()["error"]["code"] == "STORY_NOT_FOUND"

    home = client.get("/api/v1/home", headers=owner["headers"]).json()
    assert home["weeklyActivity"] == {"conversationDays": 1, "completedStories": 2} and home["resume"] is None
