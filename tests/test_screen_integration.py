"""실제 화면 계약: 활동 저장·복원, 퀴즈 복원, 프로필 전환과 보호자 공유."""

from app import db
from app.v1 import ai_gate
from app.v1.models_library import WordbookEntry

from test_v1_accounts import link_guardian, new_profile
from test_v1_activities import make_user, start


def test_all_screen_read_endpoints(client, frozen):
    user = make_user()
    profile = user["profile_id"]
    endpoints = [
        "me",
        "home",
        "stories",
        "profiles",
        f"profiles/{profile}",
        f"profiles/{profile}/settings",
        "activities",
        "activity-sessions",
        "wordbook",
        "books",
        "topic-categories",
        "topics",
        "community/stories",
        "guardian-links",
        "legal-documents",
        f"consents?profileId={profile}",
        "guardian/share-requests?status=ALL",
        "reports/progress?period=7d",
        "guardian/consultations/eligibility",
        "guardian/consultations",
        "guardian/safety-events",
        "notifications",
        "notification-settings",
        "data/overview",
        "service-info",
    ]
    for endpoint in endpoints:
        response = client.get(f"/api/v1/{endpoint}", headers=user["headers"])
        assert response.status_code == 200, (endpoint, response.text)


def test_path_saves_program_executes_and_restores(client, frozen, monkeypatch):
    monkeypatch.setattr(ai_gate, "block_reason", lambda child: "test_no_external_ai")
    user = make_user()
    session = start(client, user, "path-teaching")
    base = f"/api/v1/activity-sessions/{session['sessionId']}"
    taught = client.post(
        base + "/path/teach",
        json={
            "clientRevision": 0,
            "text": "앞으로 두 칸, 오른쪽으로 돌아, 앞으로 두 칸",
        },
        headers=user["headers"],
    )
    assert taught.status_code == 200, taught.text
    assert taught.json()["session"]["draft"]["path"]["program"]
    ran = client.post(base + "/path/run", json={"clientRevision": 1}, headers=user["headers"])
    assert ran.status_code == 200, ran.text
    path = ran.json()["session"]["draft"]["path"]
    assert path["lastRun"]["outcome"] == "arrived"
    assert path["runs"] == 1 and path["wins"] == 1
    assert client.get(base, headers=user["headers"]).json()["session"]["draft"]["path"] == path
    assert client.post(base + "/path/run", json={"clientRevision": 1}, headers=user["headers"]).status_code == 409
    other = make_user()
    assert client.get(base, headers=other["headers"]).status_code == 404
    assert client.post(base + "/path/run", json={"clientRevision": 2}, headers=other["headers"]).status_code == 404
    assert client.post(base + "/advance", headers=user["headers"]).status_code == 200
    complete = client.post(base + "/complete", headers=user["headers"])
    assert complete.status_code == 200, complete.text
    story_id = complete.json()["story"]["id"]
    story = client.get(f"/api/v1/stories/{story_id}", headers=user["headers"])
    assert story.status_code == 200 and story.json()["sourceConversation"] is None
    assert client.get("/api/v1/activity-sessions?status=ACTIVE", headers=user["headers"]).json()["items"] == []
    assert client.get("/api/v1/stories", headers=user["headers"]).json()["items"][0]["id"] == story_id


def test_quiz_restore_hides_answers_and_is_owned(client, frozen):
    user = make_user()
    with next(db.get_session()) as session:
        session.add(
            WordbookEntry(
                profile_id=user["profile_id"],
                user_id=user["id"],
                word="그림자",
                meaning="빛이 가려진 자리",
                example="그림자가 길어요.",
            )
        )
        session.commit()
    created = client.post("/api/v1/word-quizzes", json={"count": 1}, headers=user["headers"])
    assert created.status_code == 201, created.text
    quiz = created.json()
    restored = client.get(f"/api/v1/word-quizzes/{quiz['id']}", headers=user["headers"])
    assert restored.json() == quiz
    assert "answerOptionId" not in str(restored.json())
    question = quiz["questions"][0]
    answer = client.post(
        f"/api/v1/word-quizzes/{quiz['id']}/answers",
        json={"questionId": question["id"], "optionId": question["options"][0]["id"]},
        headers=user["headers"],
    )
    assert answer.status_code == 200, answer.text
    assert client.get(f"/api/v1/word-quizzes/{quiz['id']}", headers=user["headers"]).json()["status"] == "COMPLETED"
    assert client.get(f"/api/v1/word-quizzes/{quiz['id']}", headers=make_user()["headers"]).status_code == 404


def test_changing_default_does_not_resume_another_child_greeting(client, frozen):
    user = make_user()
    first = client.post("/api/v1/first-greeting/sessions", headers=user["headers"]).json()
    second = new_profile(client, user, "바다", makeDefault=True)
    resumed = client.post("/api/v1/first-greeting/sessions", headers=user["headers"]).json()
    assert first["sessionId"] != resumed["sessionId"]
    assert (
        client.get(f"/api/v1/first-greeting/sessions/{first['sessionId']}", headers=user["headers"]).status_code == 404
    )
    assert client.get("/api/v1/me", headers=user["headers"]).json()["profiles"][0]["id"] == second["id"]


def test_invited_guardian_can_read_and_review_correct_child_story(client, frozen):
    from test_v1_library import complete_story

    user = make_user()
    story = complete_story(client, user, frozen)
    request = client.post(f"/api/v1/stories/{story['id']}/share-requests", json={}, headers=user["headers"]).json()[
        "shareRequest"
    ]
    guardian = make_user()
    link_guardian(client, user, guardian, user["profile_id"], ["VIEW_STORIES", "REVIEW_SHARING"])
    query = f"?profileId={user['profile_id']}"
    listed = client.get("/api/v1/guardian/share-requests" + query, headers=guardian["headers"])
    assert listed.status_code == 200 and listed.json()["items"][0]["id"] == request["id"]
    assert client.get(f"/api/v1/stories/{story['id']}" + query, headers=guardian["headers"]).status_code == 200
    assert client.get(f"/api/v1/stories/{story['id']}", headers=guardian["headers"]).status_code == 404
    rejected = client.post(
        f"/api/v1/guardian/share-requests/{request['id']}/reject" + query,
        json={"reason": "조금 더 다듬어요"},
        headers=guardian["headers"],
    )
    assert rejected.status_code == 200, rejected.text


def test_export_and_deletion_follow_the_selected_child(client, frozen):
    from datetime import timedelta

    from app import clock
    from app.v1 import ops_data
    from app.v1.models_ops import DeletionRequest
    from app.v1.models_social import ReportSummary

    user = make_user()
    first_id = user["profile_id"]
    with next(db.get_session()) as session:
        session.add(WordbookEntry(profile_id=first_id, user_id=user["id"], word="그림자", meaning="빛이 가려진 자리"))
        session.add(
            ReportSummary(
                profile_id=first_id,
                user_id=user["id"],
                period_from="2026-09-01",
                period_to="2026-09-18",
                source_version="test",
                body={"highlights": ["내 생각을 말했어요"]},
            )
        )
        session.commit()
    start(client, user, "path-teaching")
    assert client.get("/api/v1/data/overview", headers=user["headers"]).json()["counts"]["words"] == 1
    created = client.post("/api/v1/data-exports", json={}, headers=user["headers"]).json()["job"]
    assert created["status"] == "SUCCEEDED"
    download = client.get(f"/api/v1/data-exports/{created['id']}", headers=user["headers"]).json()["download"]
    payload = client.get(download["url"]).json()
    assert payload["wordbook"][0]["word"] == "그림자"
    assert payload["activities"][0]["activityId"] == "path-teaching"
    assert len(payload["reports"]) == 1
    deletion = client.post(
        "/api/v1/data-deletion-requests",
        json={"confirmation": "DELETE", "scope": "ALL_CHILD_DATA"},
        headers=user["headers"],
    ).json()["request"]
    second = new_profile(client, user, "바다", makeDefault=True)
    with next(db.get_session()) as session:
        session.add(WordbookEntry(profile_id=second["id"], user_id=user["id"], word="바다", meaning="큰 물"))
        session.commit()
        row = session.get(DeletionRequest, deletion["id"])
        row.effective_at = clock.now() - timedelta(seconds=1)
        ops_data.execute(session, row)
        session.commit()
        assert row.status == "SUCCEEDED"
        assert row.result["words"] == 1
    assert client.get("/api/v1/wordbook", headers=user["headers"]).json()["items"][0]["word"] == "바다"
    assert client.get("/api/v1/data/overview", headers=user["headers"]).json()["counts"]["words"] == 1
    assert client.get("/api/v1/data/overview", headers=user["headers"]).json()["hiddenScopes"] == []


def test_home_uses_real_public_stories_and_profile_words(client, frozen):
    from app.v1.models_social import PublicStory

    user = make_user()
    with next(db.get_session()) as session:
        session.add(
            WordbookEntry(profile_id=user["profile_id"], user_id=user["id"], word="그림자", meaning="빛이 가려진 자리")
        )
        session.add(
            PublicStory(
                id="pub_screen",
                share_request_id="shr_screen",
                story_id="sty_screen",
                author_user_id=user["id"],
                title="실제 공개된 이야기",
                body="본문",
            )
        )
        session.add(
            PublicStory(
                id="pub_hidden",
                share_request_id="shr_hidden",
                story_id="sty_hidden",
                author_user_id=user["id"],
                title="숨긴 이야기",
                status="HIDDEN",
            )
        )
        session.commit()
    home = client.get("/api/v1/home", headers=user["headers"]).json()
    assert home["recentWords"][0]["word"] == "그림자"
    assert [s["id"] for s in home["communityStories"]] == ["pub_screen"]
    new_profile(client, user, "바다", makeDefault=True)
    assert client.get("/api/v1/home", headers=user["headers"]).json()["recentWords"] == []


def test_local_migration_preserves_legacy_rows_and_is_repeatable(tmp_path):
    import sqlite3

    from tools.migrate_local_sqlite import migrate

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE activity_sessions (id TEXT PRIMARY KEY, old_payload TEXT)")
        connection.execute("INSERT INTO activity_sessions VALUES ('legacy', 'keep me')")
        connection.execute(
            "CREATE TABLE activity_records (id TEXT PRIMARY KEY, session_id TEXT REFERENCES activity_sessions(id))"
        )
        connection.execute("INSERT INTO activity_records VALUES ('record', 'legacy')")
    report = migrate(path)
    assert report["archived"]["activity_sessions"]["rows"] == 1
    with sqlite3.connect(report["backup"]) as connection:
        assert connection.execute("SELECT old_payload FROM activity_sessions").fetchone()[0] == "keep me"
    with sqlite3.connect(path) as connection:
        table = report["archived"]["activity_sessions"]["table"]
        assert connection.execute(f'SELECT old_payload FROM "{table}"').fetchone()[0] == "keep me"
        assert connection.execute("SELECT count(*) FROM activity_sessions").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert migrate(path)["changed"] is False
    # Also repair the first integration's old dependent table without losing its row.
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE activity_records")
        connection.execute(
            "CREATE TABLE activity_records (id TEXT PRIMARY KEY, session_id TEXT REFERENCES activity_sessions(id))"
        )
        connection.execute("INSERT INTO activity_records VALUES ('record', 'legacy')")
    fixed = migrate(path)
    assert fixed["repairedLegacyReferences"][0]["table"] == "activity_records"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT session_id FROM activity_records").fetchone()[0] == "legacy"


def test_family_public_copy_is_not_visible_to_unrelated_accounts(client, frozen):
    from app.v1.models_social import PublicStory

    from test_v1_library import complete_story

    owner, stranger = make_user(), make_user()
    story = complete_story(client, owner, frozen)
    with next(db.get_session()) as session:
        session.add(
            PublicStory(
                id="pub_family",
                share_request_id="shr_family",
                story_id=story["id"],
                author_user_id=owner["id"],
                audience="FAMILY",
                title="가족에게만",
            )
        )
        session.commit()
    assert client.get("/api/v1/community/stories/pub_family", headers=stranger["headers"]).status_code == 404
    assert client.get("/api/v1/community/stories", headers=stranger["headers"]).json()["items"] == []
    assert client.get("/api/v1/home", headers=stranger["headers"]).json()["communityStories"] == []
    assert client.get("/api/v1/community/stories/pub_family", headers=owner["headers"]).status_code == 200
    link_guardian(client, owner, stranger, owner["profile_id"], ["VIEW_STORIES"])
    assert client.get("/api/v1/community/stories/pub_family", headers=stranger["headers"]).status_code == 200
