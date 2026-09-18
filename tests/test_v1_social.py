"""v1 공유·친구 이야기·성장 리포트·안전 운영·보호자 상담.

공유 흐름(버전 불일치 409·승인 전 취소·공개 중단·수정 뒤 자동 중단), 친구 이야기(신원 감추기·추천 한 번·신고 자동 숨김),
실제 대화·이야기 행에서 센 리포트, 요약 재사용과 STALE, 운영자 허용 목록, 상담 자격과 생성(AI 모의·폴백), 계정 간 404.
"""

from __future__ import annotations

from datetime import timedelta

from app import clock, db
from app.config import get_settings
from app.models import SafetyEvent
from app.v1 import ai_gate
from app.v1.accounts import issue_access_token
from app.v1.models import User
from app.v1.models_auth import AuthIdentity
from app.v1.models_conversation import ChildProfile, StoryRecord
from app.v1.models_social import CommunityReport, PublicStory
from app.v1.report_schemas import ConsultationAnswerLLM, MonthlyConsultationLLM, ReportSummaryLLM
from sqlalchemy import select

from conftest import auth, tick
from test_v1_conversations import make_user, ready_story

V1 = "/api/v1"


# --- 도우미 ----------------------------------------------------------------------


def new_user(**kwargs) -> dict:
    """테스트가 시계를 한 달씩 옮기므로 access token 을 오래 가는 것으로 바꿔 둔다."""
    user = make_user(**kwargs)
    session = next(db.get_session())
    raw = issue_access_token(session, session.get(User, user["id"]), ttl=60 * 60 * 24 * 400)
    session.commit()
    user["headers"] = auth(raw)
    return user


def make_profile(user: dict, *, nickname: str = "별", band: str = "초등학교 2학년") -> str:
    """첫인사를 거치지 않고 프로필만 만든다(리포트·보호자 API 는 프로필 단위로 움직인다)."""
    session = next(db.get_session())
    profile = ChildProfile(
        child_id=user["child_id"],
        user_id=user["id"],
        nickname=nickname,
        school_or_group="초등학교",
        grade_or_age_band=band,
        completed_at=clock.now(),
    )
    session.add(profile)
    session.commit()
    user["profile_id"] = profile.id
    return profile.id


def finish_story(client, user: dict, frozen: dict, topic_id: str = "topic_ice_cup") -> dict:
    conversation = ready_story(client, user, frozen, topic_id)
    res = client.post(f"{V1}/conversations/{conversation['id']}/complete", json={}, headers=user["headers"])
    assert res.status_code == 200, res.text
    return res.json()["story"]


def bump_story_version(story_id: str, body: str = "고쳐 쓴 본문이야.") -> int:
    """B2 트랙의 이야기 수정 API 대신, 본문이 바뀐 상태를 직접 만든다."""
    session = next(db.get_session())
    story = session.get(StoryRecord, story_id)
    story.body = body
    story.version += 1
    story.updated_at = clock.now()
    session.commit()
    return story.version


def request_share(client, user: dict, story_id: str, **body) -> dict:
    res = client.post(
        f"{V1}/stories/{story_id}/share-requests",
        json={"audience": "PEERS", "hideProfile": True, **body},
        headers=user["headers"],
    )
    assert res.status_code == 201, res.text
    return res.json()["shareRequest"]


def approve(client, user: dict, request_id: str, version: int):
    return client.post(
        f"{V1}/guardian/share-requests/{request_id}/approve",
        json={"confirmedBodyVersion": version, "confirmedRedactions": True},
        headers=user["headers"],
    )


def publish_story(client, user: dict, frozen: dict, topic_id: str = "topic_ice_cup", **share) -> dict:
    """이야기 하나를 완성해 승인까지 마치고 공개본을 돌려준다."""
    story = finish_story(client, user, frozen, topic_id)
    request = request_share(client, user, story["id"], **share)
    res = approve(client, user, request["id"], story["version"])
    assert res.status_code == 200, res.text
    return {"story": story, "request": res.json()["shareRequest"], "public": res.json()["publicStory"]}


def make_admin(monkeypatch, user: dict, kakao_id: str = "9001") -> None:
    session = next(db.get_session())
    session.add(AuthIdentity(user_id=user["id"], provider="KAKAO", provider_user_id=kakao_id))
    session.commit()
    monkeypatch.setattr(get_settings(), "admin_kakao_ids", (kakao_id,))


def allow_ai(monkeypatch, fake) -> None:
    """AI 를 켜고 호출을 가로챈다. fake 가 None 을 주면 그 호출은 규칙 기반으로 넘어간다(대화 턴 등)."""
    monkeypatch.setattr(ai_gate, "ai_block_reason", lambda child: None)

    def dispatch(**kwargs):
        out = fake(**kwargs)
        if out is None:
            raise ai_gate.LlmError("test_no_ai", "이 호출은 규칙 기반으로 둔다")
        return out

    monkeypatch.setattr(ai_gate, "call_structured", dispatch)


# --- 공유와 보호자 승인 ------------------------------------------------------------


def test_share_flow_from_request_to_published_and_version_mismatch(client, frozen):
    user = new_user(nickname="별")
    make_profile(user)
    story = finish_story(client, user, frozen)

    request = request_share(client, user, story["id"], hideProfile=False)
    assert request["status"] == "PENDING_GUARDIAN"
    assert request["audience"] == "PEERS" and request["requestedBodyVersion"] == story["version"]
    assert request["publicStoryId"] is None and request["confirmedBodyVersion"] is None

    # 같은 이야기를 두 번 올릴 수 없다.
    duplicate = client.post(f"{V1}/stories/{story['id']}/share-requests", json={}, headers=user["headers"])
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "SHARE_ALREADY_REQUESTED"

    pending = client.get(f"{V1}/guardian/share-requests", headers=user["headers"]).json()
    assert [i["id"] for i in pending["items"]] == [request["id"]] and pending["nextCursor"] is None

    # 보호자가 다른 버전을 확인했다고 하면 승인하지 않는다.
    mismatch = approve(client, user, request["id"], story["version"] + 1)
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "SHARE_VERSION_MISMATCH"
    assert mismatch.json()["error"]["details"]["currentBodyVersion"] == story["version"]

    approved = approve(client, user, request["id"], story["version"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["shareRequest"]["status"] == "PUBLISHED"
    assert body["shareRequest"]["confirmedBodyVersion"] == story["version"]
    public = body["publicStory"]
    assert public["id"].startswith("pub_") and public["guardianApproved"] is True
    assert public["title"] == story["title"] and public["author"]["displayName"] == "별"
    assert public["author"]["ageBand"] == "초등 저학년"

    seen = client.get(f"{V1}/share-requests/{request['id']}", headers=user["headers"]).json()
    assert seen["shareRequest"]["publicStoryId"] == public["id"]
    assert client.get(f"{V1}/guardian/share-requests", headers=user["headers"]).json()["items"] == []


def test_share_request_cancel_only_before_approval(client, frozen):
    user = new_user()
    make_profile(user)
    story = finish_story(client, user, frozen)
    request = request_share(client, user, story["id"])

    cancelled = client.delete(f"{V1}/share-requests/{request['id']}", headers=user["headers"])
    assert cancelled.status_code == 200
    assert cancelled.json()["shareRequest"]["status"] == "CANCELLED"
    assert cancelled.json()["publicStory"] is None
    assert approve(client, user, request["id"], story["version"]).json()["error"]["code"] == "SHARE_NOT_PENDING"

    # 취소했으니 다시 올릴 수 있다. 승인 뒤에는 취소가 막힌다.
    again = request_share(client, user, story["id"])
    assert approve(client, user, again["id"], story["version"]).status_code == 200
    late = client.delete(f"{V1}/share-requests/{again['id']}", headers=user["headers"])
    assert late.status_code == 409 and late.json()["error"]["code"] == "SHARE_NOT_CANCELLABLE"


def test_guardian_reject_and_revoke_take_the_story_down(client, frozen):
    user, reader = new_user(), new_user()
    make_profile(user)
    story = finish_story(client, user, frozen)
    request = request_share(client, user, story["id"])

    rejected = client.post(
        f"{V1}/guardian/share-requests/{request['id']}/reject",
        json={"reason": "조금 더 다듬은 뒤에 보여 주자."},
        headers=user["headers"],
    )
    assert rejected.status_code == 200
    assert rejected.json()["shareRequest"]["status"] == "REJECTED"
    assert rejected.json()["shareRequest"]["rejectReason"] == "조금 더 다듬은 뒤에 보여 주자."

    published = publish_story(client, user, frozen, "topic_snow")
    public_id = published["public"]["id"]
    assert client.get(f"{V1}/community/stories/{public_id}", headers=reader["headers"]).status_code == 200

    revoked = client.post(
        f"{V1}/guardian/share-requests/{published['request']['id']}/revoke", json={}, headers=user["headers"]
    )
    assert revoked.status_code == 200 and revoked.json()["shareRequest"]["status"] == "REVOKED"
    assert revoked.json()["publicStory"] is None
    assert client.get(f"{V1}/community/stories/{public_id}", headers=reader["headers"]).status_code == 404
    assert client.get(f"{V1}/community/stories", headers=reader["headers"]).json()["items"] == []


def test_story_edited_after_approval_pauses_the_public_copy(client, frozen):
    user, reader = new_user(nickname="별"), new_user()
    make_profile(user)
    published = publish_story(client, user, frozen)
    public_id = published["public"]["id"]
    before = client.get(f"{V1}/community/stories/{public_id}", headers=reader["headers"]).json()["story"]

    new_version = bump_story_version(published["story"]["id"])

    # 공개본은 그대로 두고 공개만 멈춘다. 요청은 다시 승인 대기로 돌아간다.
    assert client.get(f"{V1}/community/stories/{public_id}", headers=reader["headers"]).status_code == 404
    seen = client.get(f"{V1}/share-requests/{published['request']['id']}", headers=user["headers"]).json()
    assert seen["shareRequest"]["status"] == "PENDING_GUARDIAN"
    assert seen["shareRequest"]["pendingReason"] == "STORY_EDITED"
    assert seen["publicStory"] is None
    session = next(db.get_session())
    assert session.get(PublicStory, public_id).body == before["body"]

    # 이전 버전으로는 승인할 수 없고, 새 버전을 확인해야 다시 공개된다.
    assert approve(client, user, published["request"]["id"], new_version - 1).status_code == 409
    again = approve(client, user, published["request"]["id"], new_version)
    assert again.status_code == 200 and again.json()["shareRequest"]["status"] == "PUBLISHED"
    reopened = client.get(f"{V1}/community/stories/{public_id}", headers=reader["headers"]).json()["story"]
    assert reopened["body"] == "고쳐 쓴 본문이야."


# --- 친구들의 이야기 -------------------------------------------------------------


def test_community_list_hides_identity_and_carries_a_reason(client, frozen):
    author, reader = new_user(nickname="별"), new_user()
    make_profile(author, nickname="별", band="초등학교 2학년")
    make_profile(reader, nickname="구름", band="초등학교 3학년")
    hidden = publish_story(client, author, frozen, "topic_ice_cup", hideProfile=True)
    tick(frozen, 60)
    shown = publish_story(client, author, frozen, "topic_snow", hideProfile=False)

    res = client.get(f"{V1}/community/stories", headers=reader["headers"])
    assert res.status_code == 200
    items = res.json()["items"]
    assert [i["id"] for i in items] == [shown["public"]["id"], hidden["public"]["id"]]
    assert items[0]["author"] == {"displayName": "별", "ageBand": "초등 저학년"}
    assert items[1]["author"] == {"displayName": "친구", "ageBand": "초등 저학년"}
    assert all(i["recommendationReason"] for i in items)
    assert items[0]["recommendationReason"] == "비슷한 나이의 친구가 만든 이야기"
    # 실제 학교·정확한 학년은 어떤 응답에도 없다.
    assert "2학년" not in res.text and "초등학교" not in res.text and "구름" not in res.text

    science = client.get(f"{V1}/community/stories?category=SCIENCE", headers=reader["headers"]).json()
    assert len(science["items"]) == 2
    assert client.get(f"{V1}/community/stories?category=FEELINGS", headers=reader["headers"]).json()["items"] == []
    assert client.get(f"{V1}/community/stories?cursor=zzz", headers=reader["headers"]).status_code == 400

    detail = client.get(f"{V1}/community/stories/{shown['public']['id']}", headers=reader["headers"]).json()["story"]
    assert detail["body"] and detail["mine"] is False
    assert detail["thoughtJourney"]["initialIdea"]


def test_recommendation_is_once_per_user(client, frozen):
    author, one, two = new_user(nickname="별"), new_user(), new_user()
    make_profile(author)
    published = publish_story(client, author, frozen)
    public_id = published["public"]["id"]
    url = f"{V1}/community/stories/{public_id}/recommendation"

    first = client.put(url, headers=one["headers"]).json()
    assert first == {"storyId": public_id, "recommendationCount": 1, "recommendedByMe": True}
    assert client.put(url, headers=one["headers"]).json()["recommendationCount"] == 1  # 같은 사람은 한 번만
    assert client.put(url, headers=two["headers"]).json()["recommendationCount"] == 2

    listed = client.get(f"{V1}/community/stories", headers=one["headers"]).json()["items"][0]
    assert listed["recommendationCount"] == 2 and listed["recommendedByMe"] is True
    assert client.get(f"{V1}/community/stories", headers=author["headers"]).json()["items"][0]["recommendedByMe"] is False

    removed = client.delete(url, headers=one["headers"]).json()
    assert removed == {"storyId": public_id, "recommendationCount": 1, "recommendedByMe": False}
    assert client.delete(url, headers=one["headers"]).json()["recommendationCount"] == 1  # 두 번 눌러도 같다

    session = next(db.get_session())
    assert len(list(session.scalars(select(PublicStory)))) == 1


def test_reports_over_threshold_auto_hide_and_queue_for_the_operator(client, frozen):
    author = new_user(nickname="별")
    make_profile(author)
    published = publish_story(client, author, frozen)
    public_id = published["public"]["id"]
    url = f"{V1}/community/stories/{public_id}/reports"
    readers = [new_user() for _ in range(3)]

    first = client.post(url, json={"reason": "SCARY", "detail": "무서운 표현이 있어요."}, headers=readers[0]["headers"])
    assert first.status_code == 201
    assert first.json()["report"]["status"] == "OPEN" and first.json()["storyStatus"] == "PUBLISHED"
    # 같은 사람이 또 신고해도 한 건이다.
    assert client.post(url, json={"reason": "SCARY"}, headers=readers[0]["headers"]).json()["report"]["id"] == first.json()["report"]["id"]  # noqa: E501

    assert client.post(url, json={"reason": "MEAN_WORDS"}, headers=readers[1]["headers"]).json()["storyStatus"] == "PUBLISHED"  # noqa: E501
    third = client.post(url, json={"reason": "OTHER"}, headers=readers[2]["headers"])
    assert third.json()["storyStatus"] == "HIDDEN"

    # 숨긴 뒤에는 아무에게도 보이지 않지만, 신고는 운영자 검토 대기(OPEN)로 남는다.
    assert client.get(f"{V1}/community/stories", headers=readers[0]["headers"]).json()["items"] == []
    assert client.get(f"{V1}/community/stories/{public_id}", headers=readers[0]["headers"]).status_code == 404
    session = next(db.get_session())
    assert [r.status for r in session.scalars(select(CommunityReport))] == ["OPEN", "OPEN", "OPEN"]
    assert session.get(PublicStory, public_id).report_count == 3


def test_personal_info_report_hides_at_once(client, frozen):
    author, reader = new_user(nickname="별"), new_user()
    make_profile(author)
    published = publish_story(client, author, frozen)
    res = client.post(
        f"{V1}/community/stories/{published['public']['id']}/reports",
        json={"reason": "PERSONAL_INFO", "detail": "이름이 적혀 있어요."},
        headers=reader["headers"],
    )
    assert res.status_code == 201 and res.json()["storyStatus"] == "HIDDEN"


# --- 나의 발자국과 성장 리포트 ----------------------------------------------------


def test_progress_counts_come_from_real_conversation_and_story_rows(client, frozen):
    user = new_user(nickname="별")
    profile_id = make_profile(user)

    empty = client.get(f"{V1}/reports/progress", headers=user["headers"]).json()
    assert empty["profileId"] == profile_id
    assert empty["activity"] == {"activeDays": 0, "completedStories": 0, "continuedStories": 0, "newWords": 0}
    assert empty["timeline"] == [] and empty["categoryBreakdown"] == []
    assert "진단" in empty["notice"]

    finish_story(client, user, frozen)  # 선택지 2번 + 문장 4번
    ready_story(client, user, frozen, "topic_snow")  # 아직 끝내지 않은 대화

    body = client.get(f"{V1}/reports/progress?period=30d", headers=user["headers"]).json()
    assert body["period"]["to"] >= body["period"]["from"]
    assert body["activity"]["completedStories"] == 1
    assert body["activity"]["continuedStories"] == 1
    assert body["activity"]["activeDays"] == 1
    observed = body["observedBehaviors"]
    assert observed["fullSentenceResponses"] == 8  # 두 대화에서 문장으로 답한 횟수
    assert observed["reasonExplanations"] >= 2
    assert observed["alternativeIdeas"] >= 2
    assert observed["revisedIdeas"] >= 2
    assert body["categoryBreakdown"] == [{"category": "SCIENCE", "completedStories": 1}]
    assert len(body["timeline"]) == 1 and body["timeline"][0]["completedStories"] == 1

    # 기간 밖으로 나가면 세지 않는다.
    tick(frozen, 60 * 60 * 24 * 40)
    later = client.get(f"{V1}/reports/progress?period=7d", headers=user["headers"]).json()
    assert later["activity"] == {"activeDays": 0, "completedStories": 0, "continuedStories": 0, "newWords": 0}
    assert client.get(f"{V1}/reports/progress?period=1y", headers=user["headers"]).status_code == 400


def test_summary_reuses_same_source_version_then_marks_older_ones_stale(client, frozen, monkeypatch):
    user = new_user(nickname="별", is_tester=True)
    make_profile(user)
    finish_story(client, user, frozen)
    today = (clock.now() + timedelta(hours=9)).date().isoformat()

    calls = []

    def fake(**kwargs):
        if kwargs["purpose"] != "report.summary":
            return None  # 대화 턴은 규칙 기반 그대로 둔다
        calls.append(kwargs["purpose"])
        return ReportSummaryLLM(
            highlights=["이번 주에는 까닭을 말한 답이 늘었어요."],
            suggestions=["아이가 만든 이야기를 함께 읽어 보세요."],
            conversation_tips=["오늘 가장 궁금했던 게 뭐야?"],
        )

    allow_ai(monkeypatch, fake)
    created = client.post(f"{V1}/reports/summaries", json={"to": today}, headers=user["headers"])
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["reused"] is False
    assert first["summary"]["source"] == "ai" and first["summary"]["status"] == "CURRENT"
    assert first["summary"]["summary"]["highlights"] == ["이번 주에는 까닭을 말한 답이 늘었어요."]
    assert first["summary"]["summary"]["evidenceStoryIds"]
    assert "진단" in first["summary"]["notice"]
    assert calls == ["report.summary"]

    # 같은 기간 + 같은 원본 버전이면 다시 만들지 않는다.
    again = client.post(f"{V1}/reports/summaries", json={"to": today}, headers=user["headers"]).json()
    assert again["reused"] is True and again["summary"]["id"] == first["summary"]["id"]
    assert calls == ["report.summary"]

    # 원본이 늘면 새로 만들고 이전 요약은 STALE.
    tick(frozen, 60)
    finish_story(client, user, frozen, "topic_snow")
    fresh = client.post(f"{V1}/reports/summaries", json={"to": today}, headers=user["headers"]).json()
    assert fresh["reused"] is False and fresh["summary"]["id"] != first["summary"]["id"]
    old = client.get(f"{V1}/reports/summaries/{first['summary']['id']}", headers=user["headers"]).json()
    assert old["summary"]["status"] == "STALE"
    assert client.get(f"{V1}/reports/summaries/{fresh['summary']['id']}", headers=user["headers"]).json()["summary"]["status"] == "CURRENT"  # noqa: E501


def test_summary_falls_back_to_rules_without_ai(client, frozen):
    user = new_user(nickname="별")
    make_profile(user)
    finish_story(client, user, frozen)
    today = (clock.now() + timedelta(hours=9)).date().isoformat()

    body = client.post(f"{V1}/reports/summaries", json={"to": today}, headers=user["headers"]).json()["summary"]
    assert body["source"] == "fallback"
    assert body["summary"]["highlights"] and body["summary"]["conversationTips"]
    assert body["summary"]["evidenceStoryIds"]
    assert body["period"]["to"] == today
    assert client.post(f"{V1}/reports/summaries", json={"from": today, "to": "2026-01-01"}, headers=user["headers"]).status_code == 400  # noqa: E501


# --- 안전과 운영 ------------------------------------------------------------------


def test_guardian_safety_events_show_kind_and_guidance_only(client, frozen):
    user = new_user()
    make_profile(user)
    session = next(db.get_session())
    session.add(SafetyEvent(child_id=user["child_id"], category="self_harm", escalate=True))
    session.add(SafetyEvent(child_id=user["child_id"], category="sexual", escalate=False))
    session.commit()

    res = client.get(f"{V1}/guardian/safety-events", headers=user["headers"])
    assert res.status_code == 200
    body = res.json()
    assert {i["category"] for i in body["items"]} == {"self_harm", "sexual"}
    assert all(i["guidance"] for i in body["items"])
    assert [i["needsAttention"] for i in body["items"] if i["category"] == "self_harm"] == [True]
    assert "실제로 쓴 문장은 담지 않습니다" in body["notice"]
    assert all("content" not in i and "text" not in i for i in body["items"])
    assert client.get(f"{V1}/guardian/safety-events", headers=new_user()["headers"]).status_code == 404


def test_admin_endpoints_need_the_kakao_allowlist(client, frozen, monkeypatch):
    author, reader, operator = new_user(nickname="별"), new_user(), new_user()
    make_profile(author)
    published = publish_story(client, author, frozen)
    public_id = published["public"]["id"]
    client.post(
        f"{V1}/community/stories/{public_id}/reports",
        json={"reason": "PERSONAL_INFO", "detail": "이름이 적혀 있어요."},
        headers=reader["headers"],
    )

    # 허용 목록에 없으면 403.
    denied = client.get(f"{V1}/admin/community/reports", headers=reader["headers"])
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "FORBIDDEN"
    assert client.get(f"{V1}/admin/safety-events", headers=reader["headers"]).status_code == 403

    make_admin(monkeypatch, operator)
    queue = client.get(f"{V1}/admin/community/reports", headers=operator["headers"])
    assert queue.status_code == 200
    item = queue.json()["items"][0]
    assert item["publicStoryId"] == public_id and item["status"] == "OPEN"
    assert item["storyStatus"] == "HIDDEN" and item["storyReportCount"] == 1

    kept = client.post(
        f"{V1}/admin/community/reports/{item['id']}/resolve",
        json={"resolution": "KEEP", "note": "문제 없음"},
        headers=operator["headers"],
    )
    assert kept.status_code == 200
    assert kept.json()["report"]["resolution"] == "KEEP" and kept.json()["report"]["storyStatus"] == "PUBLISHED"
    assert client.get(f"{V1}/community/stories/{public_id}", headers=reader["headers"]).status_code == 200
    assert client.get(f"{V1}/admin/community/reports", headers=operator["headers"]).json()["items"] == []

    # 지우면 공개본만 사라지고 아이 원본은 남는다.
    removed = client.post(
        f"{V1}/admin/community/reports/{item['id']}/resolve", json={"resolution": "DELETE"}, headers=operator["headers"]
    )
    assert removed.json()["report"]["storyStatus"] == "DELETED"
    assert client.get(f"{V1}/community/stories/{public_id}", headers=reader["headers"]).status_code == 404
    assert client.get(f"{V1}/stories/{published['story']['id']}", headers=author["headers"]).status_code == 200
    assert client.post(f"{V1}/admin/community/reports/nope/resolve", json={"resolution": "HIDE"}, headers=operator["headers"]).status_code == 404  # noqa: E501


def test_admin_safety_events_list_high_risk_first(client, frozen, monkeypatch):
    operator = new_user()
    make_profile(operator)
    session = next(db.get_session())
    session.add(SafetyEvent(child_id=operator["child_id"], category="self_harm", escalate=True))
    session.add(SafetyEvent(child_id=operator["child_id"], category="sexual", escalate=False))
    session.commit()
    make_admin(monkeypatch, operator)

    escalated = client.get(f"{V1}/admin/safety-events", headers=operator["headers"]).json()
    assert [i["category"] for i in escalated["items"]] == ["self_harm"]
    assert escalated["items"][0]["profileId"] == operator["profile_id"]
    everything = client.get(f"{V1}/admin/safety-events?escalatedOnly=false", headers=operator["headers"]).json()
    assert len(everything["items"]) == 2


# --- 보호자 월간 상담 ------------------------------------------------------------


def test_consultation_eligibility_gate_then_generation_with_ai(client, frozen, monkeypatch):
    user = new_user(nickname="별", is_tester=True)
    profile_id = make_profile(user)

    blank = client.get(f"{V1}/guardian/consultations/eligibility", headers=user["headers"]).json()
    assert blank == {
        "profileId": profile_id, "eligible": False, "period": "2026-08", "reason": "아직 이야기 기록이 없어요.",
        "daysRemaining": 30, "completedStories": 0, "alreadyCreated": False,
    }  # fmt: skip

    finish_story(client, user, frozen)
    early = client.get(f"{V1}/guardian/consultations/eligibility?period=2026-09", headers=user["headers"]).json()
    assert early["eligible"] is False and early["daysRemaining"] == 30
    blocked = client.post(f"{V1}/guardian/consultations", json={"period": "2026-09"}, headers=user["headers"])
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "CONSULTATION_NOT_ELIGIBLE"

    tick(frozen, 60 * 60 * 24 * 31)  # 한 달 이용 뒤
    ready = client.get(f"{V1}/guardian/consultations/eligibility", headers=user["headers"]).json()
    assert ready == {
        "profileId": profile_id, "eligible": True, "period": "2026-09", "reason": "이 달 활동으로 상담을 만들 수 있어요.",
        "daysRemaining": 0, "completedStories": 1, "alreadyCreated": False,
    }  # fmt: skip

    def fake(**kwargs):
        if kwargs["purpose"] not in ("guardian.consultation.v1", "guardian.consultation.question"):
            return None  # 대화 턴은 규칙 기반 그대로 둔다
        if kwargs["purpose"] == "guardian.consultation.question":
            return ConsultationAnswerLLM(answer="이야기를 다시 읽으며 까닭을 물어봐 주세요.")
        return MonthlyConsultationLLM(
            observed_behaviors=["까닭을 문장으로 말한 적이 여러 번 있었어요."],
            examples=["아이 말: “왜냐하면 차가운 캔에도 물방울이 생겼기 때문이야.”"],
            questions_to_try=["그때 왜 그렇게 생각했어?"],
        )

    allow_ai(monkeypatch, fake)
    created = client.post(f"{V1}/guardian/consultations", json={}, headers=user["headers"])
    assert created.status_code == 201, created.text
    consultation = created.json()["consultation"]
    assert consultation["period"] == "2026-09" and consultation["source"] == "ai"
    assert consultation["consultation"]["observedBehaviors"] == ["까닭을 문장으로 말한 적이 여러 번 있었어요."]
    assert consultation["consultation"]["evidenceStoryIds"]
    assert "진단" in consultation["notice"]

    # 한 달에 한 번. 다시 불러도 같은 상담을 돌려준다.
    same = client.post(f"{V1}/guardian/consultations", json={"period": "2026-09"}, headers=user["headers"]).json()
    assert same["consultation"]["id"] == consultation["id"]
    after = client.get(f"{V1}/guardian/consultations/eligibility", headers=user["headers"]).json()
    assert after["eligible"] is False and after["alreadyCreated"] is True

    listed = client.get(f"{V1}/guardian/consultations", headers=user["headers"]).json()
    assert [i["id"] for i in listed["items"]] == [consultation["id"]] and listed["nextCursor"] is None

    asked = client.post(
        f"{V1}/guardian/consultations/{consultation['id']}/questions",
        json={"question": "아이가 이유를 말할 때 어떻게 도와주면 좋을까요?"},
        headers=user["headers"],
    )
    assert asked.status_code == 201
    assert asked.json()["question"]["source"] == "ai"
    assert asked.json()["question"]["answer"] == "이야기를 다시 읽으며 까닭을 물어봐 주세요."
    detail = client.get(f"{V1}/guardian/consultations/{consultation['id']}", headers=user["headers"]).json()
    assert len(detail["consultation"]["questions"]) == 1


def test_consultation_falls_back_to_template_without_ai(client, frozen):
    user = new_user(nickname="별")
    make_profile(user)
    finish_story(client, user, frozen)
    tick(frozen, 60 * 60 * 24 * 31)

    created = client.post(f"{V1}/guardian/consultations", json={}, headers=user["headers"])
    assert created.status_code == 201, created.text
    consultation = created.json()["consultation"]
    assert consultation["source"] == "fallback"
    assert consultation["consultation"]["observedBehaviors"] and consultation["consultation"]["questionsToTry"]
    assert consultation["consultation"]["evidenceStoryIds"]

    asked = client.post(
        f"{V1}/guardian/consultations/{consultation['id']}/questions",
        json={"question": "아이가 또래보다 느린 걸까요?"},
        headers=user["headers"],
    )
    assert asked.json()["question"]["source"] == "fallback"
    assert "진단" in asked.json()["question"]["answer"] or "판단" in asked.json()["question"]["answer"]


# --- 계정 간 접근 ----------------------------------------------------------------


def test_other_accounts_cannot_reach_my_records(client, frozen, monkeypatch):
    owner, other = new_user(nickname="별"), new_user()
    owner_profile = make_profile(owner)
    make_profile(other, nickname="구름")
    published = publish_story(client, owner, frozen)
    request_id = published["request"]["id"]
    tick(frozen, 60 * 60 * 24 * 31)
    summary = client.post(f"{V1}/reports/summaries", json={}, headers=owner["headers"]).json()["summary"]
    consultation = client.post(f"{V1}/guardian/consultations", json={}, headers=owner["headers"]).json()["consultation"]

    assert client.get(f"{V1}/share-requests/{request_id}", headers=other["headers"]).status_code == 404
    assert client.delete(f"{V1}/share-requests/{request_id}", headers=other["headers"]).status_code == 404
    assert approve(client, other, request_id, 1).status_code == 404
    assert client.post(f"{V1}/guardian/share-requests/{request_id}/reject", json={"reason": "안 돼"}, headers=other["headers"]).status_code == 404  # noqa: E501
    assert client.post(f"{V1}/guardian/share-requests/{request_id}/revoke", json={}, headers=other["headers"]).status_code == 404  # noqa: E501
    assert client.post(f"{V1}/stories/{published['story']['id']}/share-requests", json={}, headers=other["headers"]).status_code == 404  # noqa: E501
    assert client.get(f"{V1}/reports/summaries/{summary['id']}", headers=other["headers"]).status_code == 404
    assert client.get(f"{V1}/guardian/consultations/{consultation['id']}", headers=other["headers"]).status_code == 404
    assert client.get(f"{V1}/guardian/consultations", headers=other["headers"]).json()["items"] == []

    # 남의 profileId 를 URL·본문에 넣어도 자기 기록만 본다.
    assert client.get(f"{V1}/reports/progress?profileId={owner_profile}", headers=other["headers"]).status_code == 404
    denied = client.post(f"{V1}/reports/summaries", json={"profileId": owner_profile}, headers=other["headers"])
    assert denied.status_code == 404 and denied.json()["error"]["code"] == "PROFILE_NOT_FOUND"

    # 토큰이 없으면 전부 401.
    for method, url in (
        ("get", f"{V1}/community/stories"),
        ("get", f"{V1}/reports/progress"),
        ("get", f"{V1}/guardian/share-requests"),
        ("get", f"{V1}/admin/community/reports"),
    ):
        assert getattr(client, method)(url).status_code == 401
