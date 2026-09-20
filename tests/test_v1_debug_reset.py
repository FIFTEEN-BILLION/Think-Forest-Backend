"""디버그 초기화: 전체 사용자 테이블, 다른 계정/기본 데이터 보존, 재가입 확인."""

from datetime import date, datetime, timedelta

import pytest
from app import db
from app.config import get_settings
from app.v1.routers import auth, debug
from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, event, select, text

URL = "/api/v1/debug/reset-account"


@pytest.fixture
def debug_settings(monkeypatch):
    settings = get_settings().model_copy(
        update={
            "debug_mode": True,
            "auth_dev_login": True,
            "auth_cookie_secure": False,
        }
    )
    monkeypatch.setattr(debug, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    return settings


def login(client, key):
    res = client.post("/api/v1/auth/dev/login", json={"deviceKey": key})
    assert res.status_code == 200, res.text
    return res.json(), {"Authorization": f"Bearer {res.json()['accessToken']}"}


def seed_every_user_table(user_id):
    """모든 사용자 테이블에 기록을 넣어 삭제 누락과 타인 데이터 삭제를 검출한다."""
    with next(db.get_session()) as session:
        tables = db.Base.metadata.tables
        user = session.execute(select(tables["users"]).where(tables["users"].c.id == user_id)).mappings().one()
        ids = {"users": user_id, "families": user["family_id"], "children": user["child_id"]}
        for table in db.Base.metadata.sorted_tables:
            if table.name in {*ids, "oauth_states", "topic_schedules", "guest_rate_limits"}:
                continue
            values = {}
            for column in table.columns:
                if column.name == "id":
                    values[column.name] = f"row_{user_id[-12:]}_{len(ids)}"
                elif column.foreign_keys:
                    fk = next(iter(column.foreign_keys))
                    values[column.name] = ids[fk.column.table.name]
                elif column.name in {"user_id", "requested_by", "created_by", "accepted_user_id"}:
                    values[column.name] = user_id
                elif column.name == "child_id":
                    values[column.name] = user["child_id"]
                elif column.name == "profile_id":
                    values[column.name] = ids.get("child_profiles")
                elif column.nullable or column.default is not None:
                    continue
                elif isinstance(column.type, Boolean):
                    values[column.name] = False
                elif isinstance(column.type, Integer):
                    values[column.name] = 1
                elif isinstance(column.type, DateTime):
                    values[column.name] = datetime.now() + timedelta(days=1)
                elif isinstance(column.type, Date):
                    values[column.name] = date.today()
                elif isinstance(column.type, JSON):
                    values[column.name] = {}
                else:
                    values[column.name] = f"test-{user_id[-12:]}"
            session.execute(table.insert().values(**values))
            if "id" in values:
                ids[table.name] = values["id"]
        # FK가 없는 참조도 실제 연결로 만들어 공개본/반응/상담 후속 질문을 검사한다.
        links = {
            "public_stories": {"story_id": ids["story_records"], "share_request_id": ids["share_requests_v1"]},
            "story_recommendations": {"public_story_id": ids["public_stories"]},
            "community_reports_v1": {"public_story_id": ids["public_stories"]},
            "consultation_questions_v1": {"consultation_id": ids["consultations_v1"]},
        }
        for name, values in links.items():
            session.execute(tables[name].update().where(tables[name].c.id == ids[name]).values(**values))
        session.commit()
        return ids


def snapshot():
    with next(db.get_session()) as session:
        return {
            table.name: [dict(row) for row in session.execute(select(table)).mappings()]
            for table in db.Base.metadata.sorted_tables
        }


def test_reset_erases_only_current_account_and_allows_fresh_login(client, debug_settings):
    # 실 DB처럼 FK를 강제하여 잘못된 삭제 순서도 검증한다.
    with db.engine().connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
    other, other_headers = login(client, "other-debug-user")
    seed_every_user_table(other["user"]["id"])
    with next(db.get_session()) as session:
        schedule = db.Base.metadata.tables["topic_schedules"]
        session.execute(
            schedule.insert().values(
                topic_id="topic_ice_cup",
                starts_on=date.today(),
                ends_on=date.today(),
                reason="기본 편성 유지",
                created_by=other["user"]["id"],
            )
        )
        session.commit()
    expected = snapshot()
    mine, headers = login(client, "current-debug-user")
    mine_id = mine["user"]["id"]
    seed_every_user_table(mine_id)
    topics_before = client.get("/api/v1/topics", headers=other_headers).json()
    res = client.post(URL, json={}, headers=headers)
    assert res.status_code == 200, res.text
    assert "Max-Age=0" in res.headers["set-cookie"]
    assert snapshot() == expected
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/token/refresh").status_code == 401
    fresh, fresh_headers = login(client, "current-debug-user")
    assert fresh["user"]["id"] != mine_id
    assert fresh["user"]["needsFirstGreeting"] is True
    assert client.get("/api/v1/profiles", headers=fresh_headers).json()["items"] == []
    # 기본 카탈로그와 다른 사용자의 주제/인증을 그대로 유지한다.
    assert client.get("/api/v1/topics", headers=other_headers).json() == topics_before


def test_reset_requires_debug_mode_and_authentication(client, debug_settings):
    assert client.post(URL, json={}).status_code == 401
    _, headers = login(client, "current-debug-user")
    debug_settings.debug_mode = False
    before = snapshot()
    assert client.post(URL, json={}, headers=headers).status_code == 404
    assert snapshot() == before


def test_reset_cannot_target_another_user(client, debug_settings):
    _, headers = login(client, "current-debug-user")
    before = snapshot()
    assert client.post(URL, json={"userId": "someone-else"}, headers=headers).status_code == 400
    assert snapshot() == before


def test_shared_profile_blocks_reset_without_partial_deletion(client, debug_settings):
    mine, headers = login(client, "current-debug-user")
    ids = seed_every_user_table(mine["user"]["id"])
    other, _ = login(client, "other-debug-user")
    with next(db.get_session()) as session:
        members = db.Base.metadata.tables["profile_members"]
        session.execute(
            members.insert().values(
                user_id=other["user"]["id"],
                profile_id=ids["child_profiles"],
                child_id=ids["children"],
            )
        )
        session.commit()
    before = snapshot()
    res = client.post(URL, json={}, headers=headers)
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "SHARED_DATA"
    assert snapshot() == before


def test_failure_rolls_back_all_deletions(client, debug_settings):
    mine, headers = login(client, "current-debug-user")
    seed_every_user_table(mine["user"]["id"])
    before = snapshot()

    def fail_before_account_delete(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM users"):
            raise RuntimeError("test-rollback")

    engine = db.engine()
    event.listen(engine, "before_cursor_execute", fail_before_account_delete)
    try:
        with pytest.raises(RuntimeError, match="test-rollback"):
            client.post(URL, json={}, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", fail_before_account_delete)
    assert snapshot() == before
