"""v1 생각 모험 활동·주제 카테고리·요일 편성 — 서버가 다시 검사하는 단계 조건과 홈 반영."""

from __future__ import annotations

import pytest
from app import clock, db
from app.config import get_settings
from app.v1.accounts import create_account, issue_access_token
from app.v1.models_activity import ActivitySession, TopicCategoryRow, TopicSchedule
from app.v1.models_auth import AuthIdentity
from app.v1.models_conversation import ChildProfile, StoryRecord
from sqlalchemy import select

from conftest import auth

ACTIVITIES = "/api/v1/activities"
SESSIONS = "/api/v1/activity-sessions"
CATEGORIES = "/api/v1/topic-categories"
SCHEDULES = "/api/v1/admin/topic-schedules"
ADMIN_KEY = "kakao-admin-1"


def make_user(admin: bool = False, **kwargs) -> dict:
    """v1 사용자 + 완료된 아이 프로필(require_profile 이 있어야 활동 API 를 쓸 수 있다)."""
    session = next(db.get_session())
    user = create_account(session, **kwargs)
    now = clock.now()
    profile = ChildProfile(
        child_id=user.child_id,
        user_id=user.id,
        nickname="별",
        grade_or_age_band="초등학교 2학년",
        interests=["공룡"],
        growth_goal="생각 말하기",
        completed_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    if admin:
        session.add(AuthIdentity(user_id=user.id, provider="KAKAO", provider_user_id=ADMIN_KEY))
    raw = issue_access_token(session, user)
    session.commit()
    return {"id": user.id, "profile_id": profile.id, "headers": auth(raw)}


@pytest.fixture
def admin_allowlist(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_kakao_ids", (ADMIN_KEY,))
    return settings


def start(client, user: dict, activity_id: str, key: str | None = None, **body) -> dict:
    headers = {**user["headers"], **({"Idempotency-Key": key} if key else {})}
    res = client.post(SESSIONS, json={"activityId": activity_id, **body}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["session"]


def save(client, user: dict, session_id: str, revision: int, **event):
    return client.patch(
        f"{SESSIONS}/{session_id}", json={"clientRevision": revision, "event": event}, headers=user["headers"]
    )


def write(client, user: dict, session: dict, text: str) -> dict:
    """글쓰기 단계 한 칸 채우고 다음 단계로."""
    saved = save(client, user, session["sessionId"], session["revision"], type="TEXT", value=text)
    assert saved.status_code == 200, saved.text
    moved = client.post(f"{SESSIONS}/{saved.json()['session']['sessionId']}/advance", headers=user["headers"])
    assert moved.status_code == 200, moved.text
    return moved.json()["session"]


def just_advance(client, user: dict, session: dict) -> dict:
    res = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert res.status_code == 200, res.text
    return res.json()["session"]


# --- 활동 목록·상세 ----------------------------------------------------------------


def test_activity_list_filters_and_detail(client, frozen):
    user = make_user()
    everything = client.get(ACTIVITIES, headers=user["headers"]).json()
    assert len(everything["items"]) == 11 and everything["nextCursor"] is None
    first = everything["items"][0]
    assert first["id"] == "path-teaching" and first["track"] == "lab" and first["area"] == "과학 · 수학 · 역사"
    assert first["place"] == "호기심 실험실" and first["minCharacters"] == 8

    forest = client.get(f"{ACTIVITIES}?track=forest", headers=user["headers"]).json()["items"]
    assert [a["id"] for a in forest] == ["honey", "seed", "umbrella"]
    theater = client.get(f"{ACTIVITIES}?area=인성", headers=user["headers"]).json()["items"]
    assert [a["id"] for a in theater] == ["kindness", "courage", "waiting"]
    found = client.get(f"{ACTIVITIES}?query=그림자", headers=user["headers"]).json()["items"]
    assert [a["id"] for a in found] == ["first-inquiry", "shadow"]  # 제목·부제·소개·태그를 함께 찾는다
    assert [a["id"] for a in client.get(f"{ACTIVITIES}?query=저울", headers=user["headers"]).json()["items"]] == ["balance"]
    assert client.get(f"{ACTIVITIES}?track=sky", headers=user["headers"]).status_code == 400

    page1 = client.get(f"{ACTIVITIES}?limit=5", headers=user["headers"]).json()
    page2 = client.get(f"{ACTIVITIES}?limit=5&cursor={page1['nextCursor']}", headers=user["headers"]).json()
    assert len(page1["items"]) == 5 and len(page2["items"]) == 5 and page2["nextCursor"]

    detail = client.get(f"{ACTIVITIES}/honey", headers=user["headers"]).json()["activity"]
    assert detail["intro"].startswith("토끼가 소풍 자리에") and "다람쥐" in detail["clue"]
    assert len(detail["steps"]) == 5 and len(detail["questions"]) == 3
    assert detail["visuals"]["kind"] == "EVIDENCE" and len(detail["visuals"]["items"]) == 3
    assert detail["minCharacters"] == 15  # 보통 난이도

    missing = client.get(f"{ACTIVITIES}/nope", headers=user["headers"])
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ACTIVITY_NOT_FOUND"


def test_activities_need_login_and_profile(client, frozen):
    assert client.get(ACTIVITIES).status_code == 401
    session = next(db.get_session())
    user = create_account(session)  # 첫인사를 아직 안 한 계정 — 프로필이 없다
    raw = issue_access_token(session, user)
    session.commit()
    res = client.get(ACTIVITIES, headers=auth(raw))
    assert res.status_code == 404 and res.json()["error"]["code"] == "PROFILE_NOT_FOUND"


# --- 활동 세션 --------------------------------------------------------------------


def test_session_start_is_idempotent_and_scoped(client, frozen):
    owner, other = make_user(), make_user()
    first = start(client, owner, "honey", key="act-1")
    again = client.post(
        SESSIONS, json={"activityId": "honey"}, headers={**owner["headers"], "Idempotency-Key": "act-1"}
    )
    assert again.json()["session"]["sessionId"] == first["sessionId"]
    assert first["sessionId"].startswith("act_") and first["status"] == "ACTIVE"
    assert first["step"] == {"index": 0, "label": "이야기 만나기", "total": 4, "writing": False}
    assert first["revision"] == 0 and first["readyToComplete"] is False

    restored = client.get(f"{SESSIONS}/{first['sessionId']}", headers=owner["headers"])
    assert restored.status_code == 200 and restored.json()["session"]["draft"]["text"] == ""
    # 다른 프로필은 있는지도 알 수 없다.
    assert client.get(f"{SESSIONS}/{first['sessionId']}", headers=other["headers"]).status_code == 404
    assert client.post(f"{SESSIONS}/{first['sessionId']}/advance", headers=other["headers"]).status_code == 404
    assert client.delete(f"{SESSIONS}/{first['sessionId']}", headers=other["headers"]).status_code == 404
    unknown = client.post(SESSIONS, json={"activityId": "nope"}, headers=owner["headers"])
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "ACTIVITY_NOT_FOUND"


def test_autosave_revision_conflict(client, frozen):
    user = make_user()
    session = start(client, user, "honey")
    session = just_advance(client, user, session)  # 0 → 1 (이야기 만나기는 조건 없음)
    ok = save(client, user, session["sessionId"], session["revision"], type="TEXT", value="발자국이 보였어요")
    assert ok.status_code == 200 and ok.json()["session"]["revision"] == session["revision"] + 1
    assert ok.json()["session"]["draft"]["text"] == "발자국이 보였어요"

    stale = save(client, user, session["sessionId"], session["revision"], type="TEXT", value="늦게 온 저장")
    assert stale.status_code == 409
    body = stale.json()["error"]
    assert body["code"] == "ACTIVITY_REVISION_CONFLICT" and body["details"]["serverRevision"] == session["revision"] + 1
    latest = client.get(f"{SESSIONS}/{session['sessionId']}", headers=user["headers"]).json()["session"]
    assert latest["draft"]["text"] == "발자국이 보였어요"  # 늦게 온 쓰기가 덮어쓰지 않았다

    bad = save(client, user, session["sessionId"], latest["revision"], type="APPROVE")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_INPUT"


def test_forest_minimum_input_blocks_advance(client, frozen):
    user = make_user()
    session = just_advance(client, user, start(client, user, "honey"))
    assert session["step"]["writing"] is True and session["minCharacters"] == 15
    blocked = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["code"] == "ACTIVITY_STEP_NOT_READY" and error["details"]["missing"] == ["MIN_TEXT"]
    assert "15자" in error["details"]["conditions"][0]["message"]

    short = save(client, user, session["sessionId"], session["revision"], type="TEXT", value="짧아요")
    still = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert short.status_code == 200 and still.status_code == 409
    assert still.json()["error"]["details"]["missing"] == ["MIN_TEXT"]


def test_lab_needs_two_observations(client, frozen):
    user = make_user()
    session = start(client, user, "shadow")
    session = just_advance(client, user, session)  # 0 → 1 준비
    session = write(client, user, session, "빛을 높이면 그림자가 짧아질 것 같아요")  # 1 → 2 예상
    blocked = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert blocked.status_code == 409 and blocked.json()["error"]["details"]["missing"] == ["LAB_OBSERVATIONS"]

    low = save(client, user, session["sessionId"], session["revision"], type="LAB_VALUE", value=20)
    assert low.json()["session"]["draft"]["lab"]["low"] is True
    half = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert half.status_code == 409  # 한 조건만으로는 못 넘어간다
    high = save(client, user, session["sessionId"], low.json()["session"]["revision"], type="LAB_VALUE", value=80)
    assert high.json()["session"]["draft"]["lab"]["high"] is True
    assert high.json()["session"]["missing"] == []
    session = just_advance(client, user, high.json()["session"])
    assert session["step"]["index"] == 3


def test_theater_guardian_preview_and_choice(client, frozen):
    user = make_user()
    session = start(client, user, "kindness", keyword="배려")
    assert session["draft"]["theater"]["keyword"] == "배려"
    session = just_advance(client, user, session)  # 0 → 1, 대본 생성
    assert session["draft"]["theater"]["story"]["title"] == "함께 만드는 작은 다리"

    blocked = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert blocked.status_code == 409 and blocked.json()["error"]["details"]["missing"] == ["GUARDIAN_PREVIEW"]
    approved = save(client, user, session["sessionId"], session["revision"], type="APPROVE")
    session = just_advance(client, user, approved.json()["session"])  # 1 → 2

    no_choice = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert no_choice.status_code == 409 and no_choice.json()["error"]["details"]["missing"] == ["THEATER_CHOICE"]
    moved = save(client, user, session["sessionId"], session["revision"], type="SCENE", value=1)
    too_early = save(client, user, session["sessionId"], moved.json()["session"]["revision"], type="SCENE", value=1)
    assert too_early.status_code == 400  # 선택 전에는 다음 장면으로 못 간다
    chose = save(client, user, session["sessionId"], moved.json()["session"]["revision"], type="CHOICE", value=1)
    picked = chose.json()["session"]["draft"]["theater"]
    assert picked["choice"] == 1 and picked["scene"] == 2 and "빨리 건너고 싶었어" in picked["story"]["scenes"][2]
    last = save(client, user, session["sessionId"], chose.json()["session"]["revision"], type="SCENE", value=1)
    session = just_advance(client, user, last.json()["session"])  # 2 → 3
    assert session["step"]["index"] == 3


def test_theater_full_run_completes_and_creates_story(client, frozen):
    user = make_user()
    session = start(client, user, "kindness", keyword="배려")
    session = just_advance(client, user, session)
    session = save(client, user, session["sessionId"], session["revision"], type="APPROVE").json()["session"]
    session = just_advance(client, user, session)
    for event in ({"type": "SCENE", "value": 1}, {"type": "CHOICE", "value": 0}, {"type": "SCENE", "value": 1}):
        session = save(client, user, session["sessionId"], session["revision"], **event).json()["session"]
    session = just_advance(client, user, session)

    early = client.post(f"{SESSIONS}/{session['sessionId']}/complete", headers=user["headers"])
    assert early.status_code == 409 and early.json()["error"]["code"] == "ACTIVITY_STEP_NOT_READY"

    session = save(client, user, session["sessionId"], session["revision"], type="EMOTION", value="뿌듯했어요").json()[
        "session"
    ]
    session = write(client, user, session, "곰에게 같이 살펴보자고 말할래요. 서로 생각이 다를 수 있으니까요.")
    assert session["step"]["index"] == 4 and session["readyToComplete"] is True

    done = client.post(
        f"{SESSIONS}/{session['sessionId']}/complete", headers={**user["headers"], "Idempotency-Key": "done-1"}
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["session"]["status"] == "COMPLETED" and body["session"]["storyId"].startswith("sty_")
    assert body["story"]["category"] == "FEELINGS" and body["story"]["answers"][0]["question"] == "친구에게 건넬 말과 그 이유"
    replay = client.post(
        f"{SESSIONS}/{session['sessionId']}/complete", headers={**user["headers"], "Idempotency-Key": "done-1"}
    )
    assert replay.json() == body

    shelf = client.get("/api/v1/stories", headers=user["headers"]).json()
    assert [s["id"] for s in shelf["items"]] == [body["story"]["id"]]
    detail = client.get(f"/api/v1/stories/{body['story']['id']}", headers=user["headers"]).json()["story"]
    assert detail["title"] == "함께 만드는 작은 다리" and detail["thoughtJourney"]["finalReflection"]
    with next(db.get_session()) as session_db:
        stored = session_db.scalars(select(StoryRecord)).all()
        assert len(stored) == 1 and stored[0].source == "fallback" and stored[0].ai_original is None

    closed = client.patch(
        f"{SESSIONS}/{session['sessionId']}",
        json={"clientRevision": 99, "event": {"type": "TEXT", "value": "더"}},
        headers=user["headers"],
    )
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "SESSION_CLOSED"
    assert client.delete(f"{SESSIONS}/{session['sessionId']}", headers=user["headers"]).status_code == 409


def test_cancel_activity_session(client, frozen):
    user = make_user()
    session = start(client, user, "honey")
    assert client.delete(f"{SESSIONS}/{session['sessionId']}", headers=user["headers"]).status_code == 204
    assert client.delete(f"{SESSIONS}/{session['sessionId']}", headers=user["headers"]).status_code == 204  # 멱등
    restored = client.get(f"{SESSIONS}/{session['sessionId']}", headers=user["headers"]).json()["session"]
    assert restored["status"] == "CANCELLED" and restored["missing"] == []
    blocked = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "SESSION_CLOSED"
    with next(db.get_session()) as session_db:
        row = session_db.get(ActivitySession, session["sessionId"])
        assert row.cancelled_at is not None and row.story_id is None


def test_first_inquiry_conditions(client, frozen):
    """시뮬레이터 활동도 기록된 사건으로 조건을 다시 센다(두 조건 관찰·판단 고르기)."""
    user = make_user()
    session = start(client, user, "first-inquiry")
    blocked = client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"])
    assert blocked.json()["error"]["details"]["missing"] == ["INQUIRY_THOUGHT"]
    for field, value in (("INITIAL", "그림자가 짧아져요"), ("REASON", "빛이 높으면 위에서 비추니까요")):
        session = save(client, user, session["sessionId"], session["revision"], type="INQUIRY", field=field, value=value).json()["session"]  # noqa: E501
    session = just_advance(client, user, session)

    assert client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"]).json()["error"][
        "details"
    ]["missing"] == ["INQUIRY_MEANING"]
    session = save(client, user, session["sessionId"], session["revision"], type="INQUIRY", field="MEANING", value="빛이 높으면 짧아진다는 뜻").json()["session"]  # noqa: E501
    assert client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"]).json()["error"][
        "details"
    ]["missing"] == ["INQUIRY_CONFIRM"]
    session = save(client, user, session["sessionId"], session["revision"], type="INQUIRY", field="CONFIRMED", value=True).json()["session"]  # noqa: E501
    session = just_advance(client, user, session)

    session = save(client, user, session["sessionId"], session["revision"], type="OBSERVATION", field="LOW_LIGHT").json()["session"]  # noqa: E501
    assert session["draft"]["inquiry"]["observed"] == ["low"]
    assert client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"]).json()["error"][
        "details"
    ]["missing"] == ["INQUIRY_CONDITIONS"]
    session = save(client, user, session["sessionId"], session["revision"], type="OBSERVATION", field="HIGH_LIGHT").json()["session"]  # noqa: E501
    session = just_advance(client, user, session)

    assert client.post(f"{SESSIONS}/{session['sessionId']}/advance", headers=user["headers"]).json()["error"][
        "details"
    ]["missing"] == ["INQUIRY_JUDGMENT"]
    for field, value in (("JUDGMENT", "keep"), ("FINAL", "빛이 높으면 짧아져요"), ("FINAL_REASON", "두 조건에서 모두 봤어요")):
        session = save(client, user, session["sessionId"], session["revision"], type="INQUIRY", field=field, value=value).json()["session"]  # noqa: E501
    session = just_advance(client, user, session)
    assert session["step"]["index"] == 4 and session["readyToComplete"] is True
    done = client.post(f"{SESSIONS}/{session['sessionId']}/complete", headers=user["headers"]).json()
    questions = [a["question"] for a in done["story"]["answers"]]
    assert questions == ["처음 생각과 이유", "내가 확인한 뜻", "내가 살펴본 조건과 결과", "처음 생각과 같아요"]
    assert done["story"]["category"] == "SCIENCE"


def test_unsafe_theater_keyword_is_refused(client, frozen):
    user = make_user()
    res = client.post(SESSIONS, json={"activityId": "courage", "keyword": "죽이기"}, headers=user["headers"])
    assert res.status_code == 422 and res.json()["error"]["code"] == "UNSAFE_CONTENT"


# --- 주제 카테고리 -----------------------------------------------------------------


def test_topic_categories_crud_and_default_protection(client, frozen):
    owner, other = make_user(), make_user()
    listing = client.get(CATEGORIES, headers=owner["headers"]).json()
    assert listing["nextCursor"] is None  # 명세 27절 목록 모양(개수가 정해져 있어 한 번에 준다)
    defaults = listing["items"]
    assert [c["id"] for c in defaults] == ["SCIENCE", "MATH", "HISTORY", "THINKING", "DAILY_LIFE"]
    assert all(c["kind"] == "DEFAULT" and c["editable"] is False for c in defaults)
    assert defaults[0]["name"] == "과학" and defaults[0]["visual"] == "magnifier"

    created = client.post(CATEGORIES, json={"name": "공룡"}, headers=owner["headers"])
    assert created.status_code == 201
    mine = created.json()["category"]
    assert mine["id"].startswith("tcat_") and mine["kind"] == "USER" and mine["editable"] is True

    assert client.post(CATEGORIES, json={"name": "공룡"}, headers=owner["headers"]).status_code == 409
    assert client.post(CATEGORIES, json={"name": "과학"}, headers=owner["headers"]).json()["error"]["code"] == "CATEGORY_EXISTS"  # noqa: E501
    unsafe = client.post(CATEGORIES, json={"name": "야한 이야기"}, headers=owner["headers"])
    assert unsafe.status_code == 422 and unsafe.json()["error"]["code"] == "UNSAFE_CATEGORY"

    renamed = client.patch(f"{CATEGORIES}/{mine['id']}", json={"name": "공룡 이야기", "order": 3}, headers=owner["headers"])
    assert renamed.status_code == 200 and renamed.json()["category"]["name"] == "공룡 이야기"
    assert renamed.json()["category"]["order"] == 3
    assert client.patch(f"{CATEGORIES}/{mine['id']}", json={}, headers=owner["headers"]).status_code == 400

    for path in (f"{CATEGORIES}/SCIENCE",):
        blocked = client.patch(path, json={"name": "과학놀이"}, headers=owner["headers"])
        assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "CATEGORY_NOT_EDITABLE"
        assert client.delete(path, headers=owner["headers"]).status_code == 403

    assert [c["id"] for c in client.get(CATEGORIES, headers=other["headers"]).json()["items"]] == [
        c["id"] for c in defaults
    ]
    assert client.patch(f"{CATEGORIES}/{mine['id']}", json={"name": "내 거"}, headers=other["headers"]).status_code == 404
    assert client.delete(f"{CATEGORIES}/{mine['id']}", headers=other["headers"]).status_code == 404

    assert client.delete(f"{CATEGORIES}/{mine['id']}", headers=owner["headers"]).status_code == 204
    assert len(client.get(CATEGORIES, headers=owner["headers"]).json()["items"]) == len(defaults)
    with next(db.get_session()) as session_db:
        assert session_db.scalars(select(TopicCategoryRow)).all() == []


def test_topic_category_limit(client, frozen):
    user = make_user()
    for i in range(20):
        assert client.post(CATEGORIES, json={"name": f"주제{i}"}, headers=user["headers"]).status_code == 201
    over = client.post(CATEGORIES, json={"name": "하나 더"}, headers=user["headers"])
    assert over.status_code == 409 and over.json()["error"]["code"] == "TOO_MANY_CATEGORIES"


# --- 요일별 주제 운영 --------------------------------------------------------------


def test_schedule_crud_is_admin_only(client, frozen, admin_allowlist):
    admin, kid = make_user(admin=True), make_user()
    assert client.get(SCHEDULES, headers=kid["headers"]).status_code == 403
    body = {
        "topicId": "topic_snow",
        "startsOn": "2026-09-14",
        "endsOn": "2026-09-20",
        "weekday": 0,
        "order": 0,
        "reason": "이번 주 월요일은 눈 이야기의 날이에요.",
    }
    assert client.post(SCHEDULES, json=body, headers=kid["headers"]).status_code == 403

    created = client.post(SCHEDULES, json=body, headers=admin["headers"])
    assert created.status_code == 201, created.text
    schedule = created.json()["schedule"]
    assert schedule["topicTitle"] == "하얀 눈" and schedule["category"] == "SCIENCE" and schedule["active"] is True

    assert client.post(SCHEDULES, json={**body, "topicId": "topic_nope"}, headers=admin["headers"]).status_code == 404
    bad_period = client.post(SCHEDULES, json={**body, "endsOn": "2026-09-10"}, headers=admin["headers"])
    assert bad_period.status_code == 400

    listed = client.get(SCHEDULES, headers=admin["headers"]).json()
    assert [s["id"] for s in listed["items"]] == [schedule["id"]] and listed["nextCursor"] is None
    assert client.get(f"{SCHEDULES}?weekday=2", headers=admin["headers"]).json()["items"] == []
    assert client.get(f"{SCHEDULES}?cursor=zzz", headers=admin["headers"]).status_code == 400
    assert len(client.get(f"{SCHEDULES}?active=true", headers=admin["headers"]).json()["items"]) == 1

    patched = client.patch(
        f"{SCHEDULES}/{schedule['id']}", json={"clearWeekday": True, "order": 2}, headers=admin["headers"]
    )
    assert patched.json()["schedule"]["weekday"] is None and patched.json()["schedule"]["order"] == 2
    assert client.patch(f"{SCHEDULES}/{schedule['id']}", json={"endsOn": "2026-01-01"}, headers=admin["headers"]).status_code == 400  # noqa: E501
    assert client.patch(f"{SCHEDULES}/nope", json={"order": 1}, headers=admin["headers"]).status_code == 404

    assert client.delete(f"{SCHEDULES}/{schedule['id']}", headers=kid["headers"]).status_code == 403
    assert client.delete(f"{SCHEDULES}/{schedule['id']}", headers=admin["headers"]).status_code == 204
    with next(db.get_session()) as session_db:
        assert session_db.scalars(select(TopicSchedule)).all() == []


def test_schedule_changes_home_recommendation_order(client, frozen, admin_allowlist):
    admin, kid = make_user(admin=True), make_user()
    before = client.get("/api/v1/home", headers=kid["headers"]).json()["recommendations"]
    assert before[0]["topicId"] != "topic_hangul"

    reason = "오늘은 한글 이야기를 함께 나눠 봐요."
    created = client.post(
        SCHEDULES,
        json={
            "topicId": "topic_hangul",
            "startsOn": "2026-09-14",
            "endsOn": "2026-09-20",
            "weekday": 0,  # 고정 시계는 2026-09-14 월요일(KST)
            "reason": reason,
        },
        headers=admin["headers"],
    )
    assert created.status_code == 201
    after = client.get("/api/v1/home", headers=kid["headers"]).json()["recommendations"]
    assert after[0]["topicId"] == "topic_hangul" and after[0]["reason"] == reason
    assert len(after) == len(before) and len({r["topicId"] for r in after}) == len(after)
    ranked = client.get("/api/v1/topics?recommended=true", headers=kid["headers"]).json()["items"]
    assert ranked[0]["id"] == "topic_hangul"

    # 요일이 다른 편성은 오늘 순서를 바꾸지 않는다.
    client.patch(f"{SCHEDULES}/{created.json()['schedule']['id']}", json={"weekday": 3}, headers=admin["headers"])
    quiet = client.get("/api/v1/home", headers=kid["headers"]).json()["recommendations"]
    assert [r["topicId"] for r in quiet] == [r["topicId"] for r in before]


def test_home_shape_with_empty_optional_sections(client, frozen):
    user = make_user()
    home = client.get("/api/v1/home", headers=user["headers"]).json()
    assert set(home) == {
        "profile",
        "recommendations",
        "resume",
        "recentWords",
        "communityStories",
        "weeklyActivity",
    }
    assert home["profile"] == {"nickname": "별", "needsFirstGreeting": False}
    assert home["resume"] is None and home["recentWords"] == [] and home["communityStories"] == []
    assert home["weeklyActivity"] == {"conversationDays": 0, "completedStories": 0}
    assert len(home["recommendations"]) == 3 and all(r["reason"] for r in home["recommendations"])


def test_home_weekly_activity_counts_completed_activity(client, frozen):
    user = make_user()
    session = start(client, user, "kindness", keyword="배려")
    session = just_advance(client, user, session)
    session = save(client, user, session["sessionId"], session["revision"], type="APPROVE").json()["session"]
    session = just_advance(client, user, session)
    for event in ({"type": "SCENE", "value": 1}, {"type": "CHOICE", "value": 0}, {"type": "SCENE", "value": 1}):
        session = save(client, user, session["sessionId"], session["revision"], **event).json()["session"]
    session = just_advance(client, user, session)
    session = save(client, user, session["sessionId"], session["revision"], type="EMOTION", value="뿌듯했어요").json()[
        "session"
    ]
    session = write(client, user, session, "곰에게 같이 살펴보자고 말할래요. 서로 생각이 다를 수 있으니까요.")
    assert client.post(f"{SESSIONS}/{session['sessionId']}/complete", headers=user["headers"]).status_code == 200

    home = client.get("/api/v1/home", headers=user["headers"]).json()
    assert home["weeklyActivity"]["completedStories"] == 1
    assert home["resume"] is None  # 활동은 대화 이어하기로 잡히지 않는다
