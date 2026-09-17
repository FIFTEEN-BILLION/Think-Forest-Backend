"""공용 픽스처 — 테스트마다 빈 메모리 DB, 고정 시계, 가족·아이 토큰."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from app import clock, db
from app.main import app
from app.services import usage
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def fresh_db():
    engine = db.configure("sqlite://")
    from app import models  # noqa: F401 — 테이블 등록
    from app.v1 import tables  # noqa: F401 — v1 테이블 등록

    db.Base.metadata.create_all(engine)
    usage.reset()
    yield
    db.Base.metadata.drop_all(engine)


@pytest.fixture
def frozen(monkeypatch):
    """2026-09-14 10:00 KST(월요일, 과학 탐험의 날)에서 멈춘 시계. state['now'] 를 옮겨 시간을 흘린다."""
    state = {"now": datetime(2026, 9, 14, 1, 0, 0)}
    monkeypatch.setattr(clock, "now", lambda: state["now"])
    return state


def tick(state: dict, seconds: int) -> None:
    state["now"] = state["now"] + timedelta(seconds=seconds)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    return TestClient(app)


def make_family(client: TestClient) -> dict:
    body = client.post("/families").json()
    return {"id": body["familyId"], "headers": auth(body["guardianToken"])}


def make_child(client: TestClient, family: dict, **payload) -> dict:
    child = client.post("/guardian/children", json=payload, headers=family["headers"]).json()
    token = client.post(f"/guardian/children/{child['id']}/devices", headers=family["headers"]).json()["childToken"]
    return {"id": child["id"], "headers": auth(token), "family": family}


@pytest.fixture
def family(client):
    return make_family(client)


@pytest.fixture
def child(client, family):
    return make_child(client, family, nickname="하늘")


def grant(client: TestClient, child: dict, **permissions) -> None:
    body = {"voice": False, "browseShared": False, "publishRequest": False, **permissions}
    r = client.put(f"/guardian/children/{child['id']}/permissions", json=body, headers=child["family"]["headers"])
    assert r.status_code == 200


SENTENCES = [
    "컵이 차가워서 물이 생긴 것 같아요.",
    "왜냐하면 차가운 음료수 캔에도 물방울이 생겼기 때문이야.",
    "냉장고에서 꺼낸 우유병에도 물이 맺혀 있었어.",
    "컵 안의 물이 새는 거라면 빈 컵에는 물방울이 안 생길 거야.",
    "내가 작아지면 공기가 차가운 컵에 닿아서 물로 바뀌는 게 보일 것 같아.",
    "그리고 공기 속에 보이지 않는 물이 있어서 그런 거라고 생각해.",
    "나는 처음 생각을 그대로 믿어. 왜냐하면 실험에서도 차가울 때만 생겼거든.",
    "여름에 안경에 김이 서리는 것도 비슷한 일 같아.",
]
COMPOSE = "처음에 나는 컵에서 물이 샌다고 생각했어. 이야기하면서 공기 속 수증기가 차가워지면 물이 된다는 걸 알게 됐어. 그래서 지금은 차가운 컵 겉면에 공기 속 물이 붙는다고 생각해."


def talk_to_story(client: TestClient, child: dict, frozen: dict, gap: int = 100) -> dict:
    """규칙 기반으로 주제 대화를 정리(compose)까지 진행해 이야기를 만든다."""
    talk = client.post("/talks", json={"topicId": "ice_cup"}, headers=child["headers"]).json()
    moves = []
    for sentence in [*SENTENCES, *SENTENCES]:
        tick(frozen, gap)
        body = client.post(f"/talks/{talk['id']}/turns", json={"text": sentence}, headers=child["headers"]).json()
        moves.append(body["move"])
        if body["move"] == "compose":
            break
    tick(frozen, gap)
    body = client.post(f"/talks/{talk['id']}/turns", json={"text": COMPOSE}, headers=child["headers"]).json()
    return {"talk": talk, "moves": moves, "final": body}
