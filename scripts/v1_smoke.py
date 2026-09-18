"""API v1 전체 흐름 점검 — 실제로 서버를 돌려 놓고 차례대로 호출한다.

사용법:
    uvicorn app.main:app --port 8000   # AUTH_DEV_LOGIN=true 로 띄운다
    python scripts/v1_smoke.py [http://127.0.0.1:8000]

성공/실패만 세어서 마지막에 표로 보여 준다. 실패는 요청·응답을 함께 찍는다.
토큰·아이 발화 원문은 찍지 않는다.
"""

from __future__ import annotations

import json
import sys
import time
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
client = httpx.Client(timeout=30)
results: list[tuple[str, bool, str]] = []


def call(label: str, method: str, path: str, *, expect: int | tuple[int, ...] = 200, **kwargs) -> dict:
    expected = (expect,) if isinstance(expect, int) else expect
    response = client.request(method, BASE + path, **kwargs)
    ok = response.status_code in expected
    note = "" if ok else f"{response.status_code} {response.text[:160]}"
    results.append((label, ok, note))
    try:
        return response.json() if response.content else {}
    except ValueError:
        return {}


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def main() -> int:
    # --- 로그인 ---
    login = call(
        "개발용 로그인", "POST", "/auth/dev/login", expect=200, json={"deviceKey": f"smoke-{uuid.uuid4().hex}"}
    )
    token = login.get("accessToken", "")
    H = {"Authorization": f"Bearer {token}"}
    call("토큰 갱신", "POST", "/auth/token/refresh", json={})
    me = call("내 정보", "GET", "/me", headers=H)
    profile = (me.get("profile") or {}).get("id")

    # --- 첫인사 ---
    greeting = call("첫인사 시작", "POST", "/first-greeting/sessions", headers={**H, **key()})
    sid = greeting.get("sessionId", "")
    call("첫인사 복원", "GET", f"/first-greeting/sessions/{sid}", headers=H)
    for text in (
        "나는 별이라고 불러 줘. 2학년이야",
        "공룡을 좋아해",
        "이빨이 커서 신기해",
        "질문하는 힘을 키우고 싶어",
    ):
        call(
            "첫인사 답하기",
            "POST",
            f"/first-greeting/sessions/{sid}/messages",
            headers=H,
            json={"clientMessageId": uuid.uuid4().hex, "input": {"type": "TEXT", "text": text}},
        )
    done = call(
        "첫인사 완료",
        "POST",
        f"/first-greeting/sessions/{sid}/complete",
        headers={**H, **key()},
        json={"trigger": "BUTTON"},
    )
    profile = (done.get("profile") or {}).get("id", profile)

    # --- 프로필·설정·동의 ---
    call("프로필 목록", "GET", "/profiles", headers=H)
    detail = call("프로필 조회", "GET", f"/profiles/{profile}", headers=H)
    version = (detail.get("profile") or {}).get("version", 1)
    call(
        "프로필 수정",
        "PATCH",
        f"/profiles/{profile}",
        headers={**H, "If-Match": f'"{version}"'},
        json={"nickname": "별"},
    )
    call("설정 조회", "GET", f"/profiles/{profile}/settings", headers=H)
    call("설정 수정", "PATCH", f"/profiles/{profile}/settings", headers=H, json={"ttsEnabled": True})
    documents = call("약관 목록", "GET", "/legal-documents?locale=ko-KR", headers=H)
    items = documents.get("items") or []
    consent_body = {
        "profileId": profile,
        "items": [{"documentId": d["id"], "version": d["version"], "agreed": True} for d in items],
        "actor": "GUARDIAN",
    }
    call("동의 기록", "POST", "/consents", headers=H, expect=201, json=consent_body)
    call("동의 조회", "GET", f"/consents?profileId={profile}", headers=H)

    # --- 보호자 연결 ---
    invitation = call(
        "초대 발급", "POST", "/guardian-links/invitations", headers=H, expect=201, json={"profileId": profile}
    )
    call("보호자 아이 목록", "GET", "/guardian/children", headers=H)
    links = call("연결 목록", "GET", "/guardian-links", headers=H)
    if links.get("items"):
        link_id = links["items"][0]["id"]
        # 프로필을 만든 계정(소유자)의 권한은 바꿀 수 없다 — 403 이 정상이다.
        call(
            "연결 권한 변경",
            "PATCH",
            f"/guardian-links/{link_id}",
            headers=H,
            expect=(200, 403),
            json={"permissions": ["VIEW_PROFILE"]},
        )
    assert invitation is not None

    # --- 주제·홈·활동 ---
    call("홈", "GET", "/home", headers=H)
    topics = call("주제 목록", "GET", "/topics?recommended=true&limit=3", headers=H)
    topic_id = (topics.get("items") or [{}])[0].get("id", "topic_ice_cup")
    call("주제 상세", "GET", f"/topics/{topic_id}", headers=H)
    call(
        "주제 만들기",
        "POST",
        "/topics",
        headers=H,
        expect=(200, 201),
        json={"title": "구름은 왜 하얄까?", "category": "SCIENCE"},
    )
    call("주제 분류", "GET", "/topic-categories", headers=H)
    category = call("분류 추가", "POST", "/topic-categories", headers=H, expect=(200, 201), json={"name": "우주"})
    category_id = (category.get("category") or {}).get("id")
    if category_id:
        call("분류 수정", "PATCH", f"/topic-categories/{category_id}", headers=H, json={"name": "우주 이야기"})
        call("분류 삭제", "DELETE", f"/topic-categories/{category_id}", headers=H, expect=(200, 204))
    call("활동 목록", "GET", "/activities", headers=H)
    activities = call("활동 상세", "GET", "/activities/honey", headers=H, expect=(200, 404))
    assert activities is not None

    # --- 이야기 대화 ---
    conversation = call(
        "대화 시작",
        "POST",
        "/conversations",
        headers={**H, **key()},
        expect=(200, 201),
        json={"topicId": topic_id, "inputMode": "TEXT", "locale": "ko-KR"},
    )
    cid = conversation.get("conversationId", "")
    interaction = conversation.get("nextInteraction") or {}
    answers = [
        "놀이터에서 봤어",
        "내 생각에는 공기 속 물이 붙은 것 같아",
        "왜냐하면 차가운 컵에만 생기니까",
        "만약 컵이 따뜻하면 안 생길 것 같아",
        "처음에는 몰랐는데 차가운 게 중요하다는 걸 알았어",
        "냉장고 음료수 캔에서도 봤어",
    ]
    last_message = ""
    for text in answers:
        time.sleep(4)  # 서버가 실제 대화 시간을 본다
        body = {"clientMessageId": uuid.uuid4().hex, "questionId": interaction.get("questionId")}
        if interaction.get("type") == "SINGLE_CHOICE" and interaction.get("options"):
            body["input"] = {"type": "SINGLE_CHOICE", "optionId": interaction["options"][0]["id"]}
        else:
            body["input"] = {"type": "TEXT", "text": text}
        turn = call("대화 답하기", "POST", f"/conversations/{cid}/messages", headers=H, json=body)
        interaction = turn.get("nextInteraction") or interaction
        last_message = (turn.get("assistantMessage") or {}).get("id", last_message)
        if turn.get("status") == "READY_TO_FINISH":
            break
    call("대화 목록", "GET", "/conversations?status=ACTIVE,READY_TO_FINISH", headers=H)
    call("대화 복원", "GET", f"/conversations/{cid}", headers=H)
    finished = call(
        "대화 완료", "POST", f"/conversations/{cid}/complete", headers={**H, **key()}, json={"trigger": "BUTTON"}
    )
    story_id = ((finished.get("story") or {}).get("id")) or ""

    # --- 책장·단어·이야기책 ---
    stories = call("책장 목록", "GET", "/stories", headers=H)
    story_id = story_id or ((stories.get("items") or [{}])[0].get("id", ""))
    story = call("책장 상세", "GET", f"/stories/{story_id}", headers=H)
    story_version = (story.get("story") or {}).get("version", 1)
    call(
        "이야기 고치기",
        "PATCH",
        f"/stories/{story_id}",
        headers={**H, "If-Match": f'"{story_version}"'},
        json={"title": "차가운 컵의 물방울"},
    )
    call("아끼는 기록", "PUT", f"/stories/{story_id}/favorite", headers=H)
    call("아끼는 기록 해제", "DELETE", f"/stories/{story_id}/favorite", headers=H, expect=(200, 204))
    if last_message:
        call(
            "낱말 담기",
            "POST",
            "/wordbook/entries",
            headers={**H, **key()},
            expect=(200, 201, 400),
            json={"word": "물방울", "messageId": last_message, "conversationId": cid},
        )
    wordbook = call("단어 보관함", "GET", "/wordbook", headers=H)
    entries = wordbook.get("items") or []
    if entries:
        entry_id = entries[0]["id"]
        call("낱말 조회", "GET", f"/wordbook/entries/{entry_id}", headers=H)
        call("낱말 고치기", "PATCH", f"/wordbook/entries/{entry_id}", headers=H, json={"status": "PRACTICING"})
    quiz = call("퀴즈 만들기", "POST", "/word-quizzes", headers=H, expect=(200, 201, 409), json={"count": 3})
    questions = (quiz.get("quiz") or quiz).get("questions") or []
    if questions:
        call(
            "퀴즈 답하기",
            "POST",
            f"/word-quizzes/{(quiz.get('quiz') or quiz)['id']}/answers",
            headers=H,
            json={"questionId": questions[0]["id"], "optionId": questions[0]["options"][0]["id"]},
        )
    book = call(
        "이야기책 만들기",
        "POST",
        "/books",
        headers={**H, **key()},
        expect=(200, 201),
        json={"title": "나의 과학책", "storyIds": [story_id]},
    )
    book_id = (book.get("book") or {}).get("id")
    if book_id:
        call("이야기책 목록", "GET", "/books", headers=H)
        call("이야기책 상세", "GET", f"/books/{book_id}", headers=H)
        call("이야기책 완성", "POST", f"/books/{book_id}/complete", headers=H)
        call("이야기책 삭제", "DELETE", f"/books/{book_id}", headers=H, expect=(200, 204))

    # --- 공유·커뮤니티 ---
    share = call(
        "공유 요청",
        "POST",
        f"/stories/{story_id}/share-requests",
        headers={**H, **key()},
        expect=(200, 201),
        json={"audience": "PEERS", "hideProfile": True},
    )
    request_id = (share.get("shareRequest") or {}).get("id")
    if request_id:
        call("공유 요청 조회", "GET", f"/share-requests/{request_id}", headers=H)
        call("보호자 승인 대기 목록", "GET", "/guardian/share-requests", headers=H)
        approved = call(
            "공유 승인",
            "POST",
            f"/guardian/share-requests/{request_id}/approve",
            headers=H,
            expect=(200, 409),
            json={"confirmedBodyVersion": story_version + 1, "confirmedRedactions": True},
        )
        public_id = ((approved.get("publicStory") or {}).get("id")) or ""
        call("친구 이야기 목록", "GET", "/community/stories", headers=H)
        if public_id:
            call("친구 이야기 상세", "GET", f"/community/stories/{public_id}", headers=H)
            call("추천", "PUT", f"/community/stories/{public_id}/recommendation", headers=H)
            call("추천 취소", "DELETE", f"/community/stories/{public_id}/recommendation", headers=H, expect=(200, 204))
            call(
                "신고",
                "POST",
                f"/community/stories/{public_id}/reports",
                headers=H,
                expect=(200, 201),
                json={"reason": "UNCOMFORTABLE_CONTENT", "detail": "무서운 표현"},
            )

    # --- 리포트·안전·상담 ---
    call("성장 리포트", "GET", f"/reports/progress?profileId={profile}&period=30d", headers=H)
    summary = call(
        "리포트 요약",
        "POST",
        "/reports/summaries",
        headers={**H, **key()},
        expect=(200, 201, 409),
        json={"profileId": profile},
    )
    summary_id = (summary.get("summary") or {}).get("id")
    if summary_id:
        call("요약 조회", "GET", f"/reports/summaries/{summary_id}", headers=H)
    call("안전 이벤트", "GET", "/guardian/safety-events", headers=H)
    call("상담 자격", "GET", f"/guardian/consultations/eligibility?profileId={profile}", headers=H)
    call("상담 목록", "GET", "/guardian/consultations", headers=H)

    # --- 음성·알림 ---
    call(
        "음성 접속권",
        "POST",
        "/speech/stream-tickets",
        headers=H,
        expect=(200, 201, 403, 503),
        json={"conversationId": cid, "locale": "ko-KR"},
    )
    call("알림 설정", "GET", "/notification-settings", headers=H)
    call("알림 설정 변경", "PATCH", "/notification-settings", headers=H, json={"shareRequests": True})
    call("알림 목록", "GET", "/notifications", headers=H)
    device = call(
        "기기 등록",
        "POST",
        "/devices",
        headers=H,
        expect=(200, 201),
        json={"pushToken": "ExponentPushToken[smoke]", "platform": "ANDROID"},
    )
    device_id = (device.get("device") or {}).get("id")
    if device_id:
        call("기기 해제", "DELETE", f"/devices/{device_id}", headers=H, expect=(200, 204))

    # --- 내 데이터 ---
    call("저장 현황", "GET", "/data/overview", headers=H)
    export = call(
        "내보내기",
        "POST",
        "/data-exports",
        headers={**H, **key()},
        expect=(200, 202),
        json={"profileId": profile, "format": "JSON"},
    )
    export_id = (export.get("job") or export.get("export") or {}).get("id")
    if export_id:
        call("내보내기 상태", "GET", f"/data-exports/{export_id}", headers=H)
    deletion = call(
        "삭제 요청",
        "POST",
        "/data-deletion-requests",
        headers={**H, **key()},
        expect=(200, 202),
        json={"profileId": profile, "scope": "ALL_CHILD_DATA", "confirmation": "DELETE", "reason": "USER_REQUEST"},
    )
    deletion_id = (deletion.get("request") or deletion.get("job") or {}).get("id")
    if deletion_id:
        call("삭제 상태", "GET", f"/data-deletion-requests/{deletion_id}", headers=H)
        call("삭제 취소", "POST", f"/data-deletion-requests/{deletion_id}/cancel", headers=H)

    call("로그아웃", "POST", "/auth/logout", headers=H, json={})

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'=' * 60}\n호출 {len(results)}건 · 성공 {passed} · 실패 {len(results) - passed}\n{'=' * 60}")
    for label, ok, note in results:
        if not ok:
            print(f"  실패 · {label}: {note}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — 점검 스크립트는 어디서 멈췄는지만 알려 주면 된다
        print("중단:", type(error).__name__, str(error)[:200])
        print(
            json.dumps([{"label": label, "ok": ok, "note": note} for label, ok, note in results], ensure_ascii=False)[
                :2000
            ]
        )
        sys.exit(2)
