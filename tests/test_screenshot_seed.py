"""스크린샷용 데이터: API 표시, 7일 집계, 재실행·프로필 격리·롤백."""

from datetime import timedelta

from app import db
from app.v1 import report_engine
from app.v1.models_library import WordbookEntry
from scripts.seed_screenshot_library import seed_library
from sqlalchemy import select

from test_v1_library import make_profile_user


def test_screenshot_seed_api_week_and_idempotency(client, frozen):
    user = make_profile_user()
    other = make_profile_user("다른 아이")
    with next(db.get_session()) as session:
        result = seed_library(session, user["profileId"])
        assert result["counts"] == {"stories": 7, "words": 9, "books": 2}
        session.commit()

    def get(path, owner=user):
        response = client.get(f"/api/v1/{path}", headers=owner["headers"])
        assert response.status_code == 200, response.text
        return response.json()

    assert len(get("stories")["items"]) == 7
    assert len(get("wordbook")["items"]) == 9
    books = get("books")["items"]
    assert len(books) == 2
    for book in books:
        assert len(get(f"books/{book['id']}")["book"]["stories"]) == 3
    for story_id in result["created"]["stories"]:
        assert get(f"stories/{story_id}")["story"]["body"]
    report = get("reports/progress?period=7d")
    assert report["activity"] == {"activeDays": 7, "completedStories": 7, "continuedStories": 0, "newWords": 9}
    assert len(report["timeline"]) == 7
    assert report["observedBehaviors"]["revisedIdeas"] == 7
    for route in ("stories", "wordbook", "books"):
        assert get(route, other)["items"] == []
    assert get("reports/progress?period=7d", other)["activity"]["newWords"] == 0

    with next(db.get_session()) as session:
        word = session.get(WordbookEntry, result["created"]["words"][0])
        word.my_sentence = "내가 직접 고친 문장은 보존한다."
        session.commit()
        assert seed_library(session, user["profileId"])["counts"] == {"stories": 0, "words": 0, "books": 0}
        session.commit()
        assert word.my_sentence == "내가 직접 고친 문장은 보존한다."


def test_seed_dry_run_rollback(frozen):
    user = make_profile_user()
    with next(db.get_session()) as session:
        seed_library(session, user["profileId"])
        session.rollback()
        assert session.scalar(select(WordbookEntry)) is None
        assert seed_library(session, user["profileId"])["counts"] == {"stories": 7, "words": 9, "books": 2}
        session.rollback()


def test_report_word_period_kst_and_version(frozen):
    user = make_profile_user()
    with next(db.get_session()) as session:
        result = seed_library(session, user["profileId"])
        today = report_engine.kst_date(frozen["now"])
        start = today - timedelta(days=6)
        records = report_engine.collect(session, user["id"], start, today, user["profileId"])
        version = records.source_version
        word = session.get(WordbookEntry, result["created"]["words"][0])
        word.created_at = report_engine.kst_midnight_utc(start) - timedelta(microseconds=1)
        session.flush()
        records = report_engine.collect(session, user["id"], start, today, user["profileId"])
        assert len(records.words) == 8
        assert records.source_version != version
        word.created_at += timedelta(microseconds=1)
        session.flush()
        assert report_engine.progress(session, user["id"], user["profileId"], "7d").activity.new_words == 9
