"""v1 운영 API — 음성 스트리밍(20절), 알림·기기(21절), 내 데이터(22절).

외부 호출(OpenAI 전사·Typecast 합성·푸시)은 전부 바꿔 끼운다. 네트워크를 쓰지 않는다.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from app import clock, db
from app.main import app
from app.models import Child, Word
from app.services import speech as speech_service
from app.services import tts as tts_service
from app.v1 import speech_engine
from app.v1.accounts import create_account, issue_access_token
from app.v1.models import User
from app.v1.models_conversation import ConversationMessage, ConversationSession, StoryRecord
from app.v1.models_ops import DeletionRequest, Notification
from fastapi.testclient import TestClient

from conftest import auth, tick

CHUNK = b"\x00\x01" * 8000  # 0.5초 분량(16kHz mono s16le)
ONE_SECOND = b"\x00\x01" * 16000
STORY_BODY = "컵이 차가워서 공기 속 물이 컵에 붙는다고 생각했어."


# ---------------------------------------------------------------- 도우미


class FakeEngine:
    """네트워크 없는 전사 엔진. partial 은 준 순서대로 한 번씩 돌려준다."""

    def __init__(self, partials: list[str], final_text: str):
        self.partials = list(partials)
        self.final_text = final_text
        self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)

    def partial(self) -> str | None:
        return self.partials.pop(0) if self.partials else None

    def final(self) -> speech_engine.FinalTranscript:
        return speech_engine.FinalTranscript(
            text=self.final_text, confidence=0.91, duration_ms=speech_engine.duration_ms(self.total)
        )

    def close(self) -> None:
        self.total = 0


def use_engine(monkeypatch, partials: list[str], final_text: str) -> None:
    monkeypatch.setattr(speech_engine, "make_engine", lambda child: FakeEngine(partials, final_text))


@pytest.fixture
def user(client, frozen):
    """v1 계정 + access token. 음성 권한은 미설정(기본 허용)."""
    session = next(db.get_session())
    account = create_account(session, is_tester=True, nickname="별")
    raw = issue_access_token(session, account)
    session.commit()
    return {"id": account.id, "childId": account.child_id, "headers": auth(raw), "token": raw}


def relogin(user: dict) -> dict:
    """access token 은 1시간이다. 시계를 크게 옮긴 뒤에는 다시 로그인한 셈 치고 새로 발급한다."""
    session = next(db.get_session())
    account = session.get(User, user["id"])
    user["headers"] = auth(issue_access_token(session, account))
    session.commit()
    return user


def issue_ticket(client: TestClient, user: dict) -> dict:
    res = client.post("/api/v1/speech/stream-tickets", json={"locale": "ko-KR"}, headers=user["headers"])
    assert res.status_code == 201, res.text
    return res.json()


# ---------------------------------------------------------------- 20. 접속권


def test_stream_ticket_is_single_use_and_expires(client, user, frozen, monkeypatch):
    use_engine(monkeypatch, [], "안녕")
    ticket = issue_ticket(client, user)
    assert ticket["streamId"].startswith("sts_")
    assert ticket["webSocketUrl"].startswith("ws://") and ticket["webSocketUrl"].endswith("/api/v1/speech/stream")
    assert ticket["expiresAt"].endswith("Z")

    # 한 번 쓰면 끝이다.
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        ws.send_json({"type": "START", "streamId": ticket["streamId"]})
        ws.send_json({"type": "STOP"})
        assert ws.receive_json()["type"] == "FINAL_TRANSCRIPT"
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        again = ws.receive_json()
    assert again == {
        "type": "ERROR",
        "code": "TICKET_INVALID",
        "retryable": False,
        "message": "연결이 만료됐어요. 마이크를 다시 눌러 주세요.",
    }

    # 30초가 지나면 쓰지 못한다.
    fresh = issue_ticket(client, user)
    tick(frozen, 31)
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={fresh['ticket']}") as ws:
        assert ws.receive_json()["code"] == "TICKET_INVALID"


def test_stream_ticket_respects_voice_disabled(client, user):
    session = next(db.get_session())
    session.get(Child, user["childId"]).permissions = {"voice": False}
    session.commit()
    res = client.post("/api/v1/speech/stream-tickets", json={}, headers=user["headers"])
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert res.json()["error"]["details"]["permission"] == "voice"


def test_stream_ticket_rejects_other_audio_format(client, user):
    res = client.post(
        "/api/v1/speech/stream-tickets",
        json={"audio": {"encoding": "OPUS", "sampleRate": 48000, "channels": 2}},
        headers=user["headers"],
    )
    assert res.status_code == 400 and res.json()["error"]["code"] == "INVALID_INPUT"


def test_voice_defaults_allow_all_speech_endpoints(client, user, monkeypatch):
    use_engine(monkeypatch, [], "안녕")
    monkeypatch.setattr(speech_service, "transcribe", lambda *args: "안녕")
    monkeypatch.setattr(tts_service, "synthesize", lambda text: (b"mp3", "audio/mpeg"))
    with next(db.get_session()) as session:
        assert "voice" not in session.get(Child, user["childId"]).permissions
    ticket = issue_ticket(client, user)
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        ws.send_json({"type": "START"})
        ws.send_bytes(ONE_SECOND)
        ws.send_json({"type": "STOP"})
        assert ws.receive_json()["type"] == "FINAL_TRANSCRIPT"
    audio = {"file": ("a.wav", speech_engine.pcm_to_wav(ONE_SECOND), "audio/wav")}
    result = client.post("/api/v1/speech/transcriptions", files=audio, headers=user["headers"])
    assert result.status_code == 200 and result.json()["text"] == "안녕"
    result = client.post("/api/v1/speech/synthesis", json={"text": "안녕"}, headers=user["headers"])
    assert result.status_code == 200


def test_disabled_voice_blocks_audio_and_previously_issued_ticket(client, user, monkeypatch):
    def unexpected_provider(*args):
        pytest.fail("꺼진 음성 설정으로 외부 제공자를 호출하면 안 된다")

    monkeypatch.setattr(speech_engine, "make_engine", unexpected_provider)
    monkeypatch.setattr(speech_service, "transcribe", unexpected_provider)
    ticket = issue_ticket(client, user)
    with next(db.get_session()) as session:
        session.get(Child, user["childId"]).permissions = {"voice": False}
        session.commit()
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        assert ws.receive_json()["code"] == "CONSENT_REQUIRED"
    result = client.post(
        "/api/v1/speech/transcriptions",
        files={"file": ("a.wav", ONE_SECOND, "audio/wav")},
        headers=user["headers"],
    )
    assert result.status_code == 403


# ---------------------------------------------------------------- 20. WebSocket


def test_stream_happy_path_sends_partials_then_final(client, user, monkeypatch):
    use_engine(monkeypatch, ["컵이", "컵이 차가워서"], "컵이 차가워서 물방울이 생긴 것 같아.")
    ticket = issue_ticket(client, user)
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        ws.send_json({"type": "START", "streamId": ticket["streamId"]})
        ws.send_bytes(CHUNK)
        first = ws.receive_json()
        ws.send_bytes(CHUNK)
        second = ws.receive_json()
        ws.send_json({"type": "STOP"})
        final = ws.receive_json()

    assert first == {"type": "PARTIAL_TRANSCRIPT", "sequence": 1, "text": "컵이", "stablePrefix": ""}
    assert second["type"] == "PARTIAL_TRANSCRIPT"
    assert second["sequence"] == 2
    assert second["text"] == "컵이 차가워서"
    assert second["stablePrefix"] == "컵이"  # 흔들리지 않는 앞부분
    assert final["type"] == "FINAL_TRANSCRIPT"
    assert final["streamId"] == ticket["streamId"]
    assert final["text"] == "컵이 차가워서 물방울이 생긴 것 같아."
    assert final["confidence"] == 0.91
    assert final["durationMs"] == 1000  # 0.5초 × 2


def test_stream_rejects_bad_ticket_and_missing_start(client, user, monkeypatch):
    use_engine(monkeypatch, [], "안녕")
    with client.websocket_connect("/api/v1/speech/stream?ticket=stk_nope") as ws:
        assert ws.receive_json()["code"] == "TICKET_INVALID"

    ticket = issue_ticket(client, user)
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        ws.send_json({"type": "STOP"})  # START 없이 보냈다
        error = ws.receive_json()
    assert error["type"] == "ERROR" and error["code"] == "INVALID_INPUT"


def test_stream_reports_no_speech(client, user, monkeypatch):
    use_engine(monkeypatch, [], "   ")
    ticket = issue_ticket(client, user)
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        ws.send_json({"type": "START", "streamId": ticket["streamId"]})
        ws.send_json({"type": "STOP"})
        error = ws.receive_json()
    assert error == {
        "type": "ERROR",
        "code": "NO_SPEECH_DETECTED",
        "retryable": True,
        "message": "목소리를 듣지 못했어요. 다시 말해 주세요.",
    }


def test_stream_warns_at_50s_and_stops_at_60s(client, user, monkeypatch):
    use_engine(monkeypatch, [], "길게 말한 이야기")
    ticket = issue_ticket(client, user)
    events = []
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        ws.send_json({"type": "START", "streamId": ticket["streamId"]})
        for _ in range(60):  # 1초짜리 조각 60개
            ws.send_bytes(ONE_SECOND)
        for _ in range(3):
            events.append(ws.receive_json())

    assert events[0]["code"] == "UTTERANCE_ENDING_SOON"  # 50초쯤 부드러운 안내
    assert events[0]["type"] == "WARNING"
    assert events[1] == {
        "type": "ERROR",
        "code": "UTTERANCE_TOO_LONG",
        "retryable": True,
        "message": "한 번에 1분까지 들을 수 있어요.",
    }
    # 상한을 넘어도 여기까지 들은 내용은 돌려준다.
    assert events[2]["type"] == "FINAL_TRANSCRIPT" and events[2]["text"] == "길게 말한 이야기"


def test_stream_says_ai_unavailable_without_api_key(client, user):
    """OPENAI_API_KEY 없이 돌리는 기본 상태 — 오류 이벤트로 알리고 끊는다."""
    ticket = issue_ticket(client, user)
    with client.websocket_connect(f"/api/v1/speech/stream?ticket={ticket['ticket']}") as ws:
        error = ws.receive_json()
    assert error["code"] == "AI_TEMPORARILY_UNAVAILABLE" and error["retryable"] is True


# ---------------------------------------------------------------- 20. 파일 재시도


def test_transcriptions_validates_mime_and_size(client, user, monkeypatch):
    use_engine(monkeypatch, [], "")
    monkeypatch.setattr(speech_service, "transcribe", lambda data, name, mime: "다시 말한 문장")

    bad = client.post(
        "/api/v1/speech/transcriptions",
        files={"file": ("a.txt", b"x", "text/plain")},
        headers=user["headers"],
    )
    assert bad.status_code == 415 and bad.json()["error"]["code"] == "UNSUPPORTED_AUDIO"

    monkeypatch.setattr(speech_service, "MAX_AUDIO_BYTES", 3)
    big = client.post(
        "/api/v1/speech/transcriptions",
        files={"file": ("a.wav", b"12345", "audio/wav")},
        headers=user["headers"],
    )
    assert big.status_code == 413 and big.json()["error"]["code"] == "AUDIO_TOO_LARGE"

    monkeypatch.setattr(speech_service, "MAX_AUDIO_BYTES", 5 * 1024 * 1024)
    ok = client.post(
        "/api/v1/speech/transcriptions",
        files={"file": ("a.wav", speech_engine.pcm_to_wav(ONE_SECOND), "audio/wav")},
        headers=user["headers"],
    )
    assert ok.status_code == 200
    assert ok.json() == {"text": "다시 말한 문장", "confidence": 0.9, "durationMs": 1000}


def test_transcriptions_report_no_speech(client, user, monkeypatch):
    use_engine(monkeypatch, [], "")
    monkeypatch.setattr(speech_service, "transcribe", lambda data, name, mime: "  ")
    res = client.post(
        "/api/v1/speech/transcriptions",
        files={"file": ("a.wav", b"12345", "audio/wav")},
        headers=user["headers"],
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "NO_SPEECH_DETECTED"


# ---------------------------------------------------------------- 20. 읽어주기


def test_synthesis_respects_voice_disabled_and_blocks_unsafe_text(client, user, monkeypatch):
    called = []
    monkeypatch.setattr(tts_service, "synthesize", lambda text: (called.append(text), (b"mp3", "audio/mpeg"))[1])

    session = next(db.get_session())
    session.get(Child, user["childId"]).permissions = {"voice": False}
    session.commit()
    denied = client.post("/api/v1/speech/synthesis", json={"text": "안녕"}, headers=user["headers"])
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "CONSENT_REQUIRED"

    session.get(Child, user["childId"]).permissions = {"voice": True}
    session.commit()
    blocked = client.post("/api/v1/speech/synthesis", json={"text": "죽여버릴거야"}, headers=user["headers"])
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "UNSAFE_CONTENT"
    assert called == []  # 금칙어는 외부 호출 전에 막는다

    ok = client.post("/api/v1/speech/synthesis", json={"text": "숲 속 이야기를 들려줄게"}, headers=user["headers"])
    assert ok.status_code == 200 and ok.content == b"mp3"
    assert ok.headers["content-type"] == "audio/mpeg"
    assert ok.headers["cache-control"] == "no-store"


def test_synthesis_reads_only_tiki_messages(client, user, monkeypatch):
    monkeypatch.setattr(tts_service, "synthesize", lambda text: (text.encode(), "audio/mpeg"))
    session = next(db.get_session())
    conversation = ConversationSession(id="cnv_test", user_id=user["id"], kind="STORY")
    session.add(conversation)
    session.add(ConversationMessage(id="msg_ai", session_id="cnv_test", seq=1, role="ASSISTANT", content="왜 그럴까?"))
    session.add(ConversationMessage(id="msg_kid", session_id="cnv_test", seq=2, role="USER", content="몰라"))
    session.commit()

    ok = client.post("/api/v1/speech/synthesis", json={"messageId": "msg_ai"}, headers=user["headers"])
    assert ok.status_code == 200 and ok.content == "왜 그럴까?".encode()
    mine = client.post("/api/v1/speech/synthesis", json={"messageId": "msg_kid"}, headers=user["headers"])
    assert mine.status_code == 403
    missing = client.post("/api/v1/speech/synthesis", json={"messageId": "msg_none"}, headers=user["headers"])
    assert missing.status_code == 404


# ---------------------------------------------------------------- 21. 기기·알림


def test_device_register_refresh_and_delete(client, user):
    token = "ExponentPushToken[abcdefghijklmnopqrst]"
    first = client.post(
        "/api/v1/devices",
        json={"platform": "ANDROID", "pushToken": token, "appVersion": "1.0.0"},
        headers=user["headers"],
    )
    assert first.status_code == 201
    device = first.json()["device"]
    assert device["id"].startswith("dev_") and device["platform"] == "ANDROID"
    assert "pushToken" not in json.dumps(first.json())  # 토큰은 응답에 싣지 않는다

    again = client.post(
        "/api/v1/devices",
        json={"platform": "ANDROID", "pushToken": token, "appVersion": "1.1.0"},
        headers=user["headers"],
    )
    assert again.json()["device"]["id"] == device["id"]  # 같은 토큰은 갱신만 한다
    assert again.json()["device"]["appVersion"] == "1.1.0"

    bad = client.post(
        "/api/v1/devices", json={"platform": "IOS", "pushToken": "not-an-expo-token"}, headers=user["headers"]
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_INPUT"

    assert client.delete(f"/api/v1/devices/{device['id']}", headers=user["headers"]).json() == {"ok": True}
    assert client.delete(f"/api/v1/devices/{device['id']}", headers=user["headers"]).status_code == 404


def test_notification_settings_list_and_read(client, user):
    from app.v1 import ops_notify

    defaults = client.get("/api/v1/notification-settings", headers=user["headers"]).json()["settings"]
    assert defaults["shareRequests"] is True and defaults["activitySummary"] is False

    patched = client.patch(
        "/api/v1/notification-settings", json={"shareRequests": False}, headers=user["headers"]
    ).json()["settings"]
    assert patched["shareRequests"] is False and patched["safetyNotices"] is True

    # 푸시 자격 증명이 없어도 알림 기록은 남는다.
    session = next(db.get_session())
    ops_notify.share_approval_requested(session, user["id"], "shr_1")
    ops_notify.safety_notice(session, user["id"], "saf_1")
    session.commit()

    listed = client.get("/api/v1/notifications", headers=user["headers"]).json()
    assert listed["unreadCount"] == 2
    assert {item["type"] for item in listed["items"]} == {"SAFETY_NOTICE", "SHARE_APPROVAL_REQUEST"}
    share = [item for item in listed["items"] if item["type"] == "SHARE_APPROVAL_REQUEST"][0]
    assert share["body"] == "확인할 공유 요청이 있어요."  # 아이 대화 내용은 들어가지 않는다
    assert share["data"] == {"shareRequestId": "shr_1"}

    read = client.post(f"/api/v1/notifications/{share['id']}/read", headers=user["headers"]).json()
    assert read["notification"]["readAt"] is not None
    first_read_at = read["notification"]["readAt"]
    again = client.post(f"/api/v1/notifications/{share['id']}/read", headers=user["headers"]).json()
    assert again["notification"]["readAt"] == first_read_at

    unread = client.get("/api/v1/notifications?unreadOnly=true", headers=user["headers"]).json()
    assert unread["unreadCount"] == 1 and len(unread["items"]) == 1
    assert client.post("/api/v1/notifications/ntf_none/read", headers=user["headers"]).status_code == 404


# ---------------------------------------------------------------- 22. 내 데이터


def seed_data(user: dict) -> None:
    """대화 2개·메시지 2개·이야기 1개·단어 1개."""
    session = next(db.get_session())
    session.add(ConversationSession(id="cnv_1", user_id=user["id"], kind="STORY"))
    session.add(ConversationSession(id="fgs_1", user_id=user["id"], kind="FIRST_GREETING"))
    session.add(ConversationMessage(id="msg_1", session_id="cnv_1", seq=1, role="ASSISTANT", content="왜 그럴까?"))
    session.add(ConversationMessage(id="msg_2", session_id="cnv_1", seq=2, role="USER", content=STORY_BODY))
    session.add(
        StoryRecord(
            id="sty_1",
            user_id=user["id"],
            session_id="cnv_1",
            category="SCIENCE",
            title="차가운 컵 이야기",
            summary="물방울이 생기는 까닭",
            body=STORY_BODY,
        )
    )
    session.add(Word(child_id=user["childId"], word="수증기", meaning="공기 속 물"))
    session.commit()


def test_data_overview_counts_and_retention(client, user):
    seed_data(user)
    body = client.get("/api/v1/data/overview", headers=user["headers"]).json()
    assert body["counts"]["conversations"] == 2
    assert body["counts"]["messages"] == 2
    assert body["counts"]["stories"] == 1
    assert body["counts"]["words"] == 1
    assert body["retention"]["deletionGraceDays"] == 7
    assert body["retention"]["accountDeletionGraceDays"] == 30
    assert body["hiddenScopes"] == [] and body["pendingDeletionRequestId"] is None


def test_export_job_download_token_is_single_use_and_expires(client, user, frozen):
    seed_data(user)
    created = client.post(
        "/api/v1/data-exports",
        json={"format": "JSON", "include": ["PROFILE", "CONVERSATIONS", "STORIES", "WORDBOOK", "REPORTS"]},
        headers={**user["headers"], "Idempotency-Key": "export-1"},
    )
    assert created.status_code == 202
    job = created.json()["job"]
    assert job["type"] == "DATA_EXPORT" and job["status"] == "SUCCEEDED" and job["id"].startswith("job_")

    replay = client.post(
        "/api/v1/data-exports", json={"format": "JSON"}, headers={**user["headers"], "Idempotency-Key": "export-1"}
    )
    assert replay.json()["job"]["id"] == job["id"]  # 재시도해도 작업이 하나다

    detail = client.get(f"/api/v1/data-exports/{job['id']}", headers=user["headers"]).json()
    assert detail["byteSize"] > 0
    url = detail["download"]["url"]

    downloaded = client.get(url)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-disposition"].endswith(f'{job["id"]}.json"')
    payload = downloaded.json()
    assert payload["stories"][0]["body"] == STORY_BODY
    assert len(payload["conversations"]) == 2 and payload["wordbook"][0]["word"] == "수증기"

    assert client.get(url).status_code == 404  # 1회용

    expiring = client.get(f"/api/v1/data-exports/{job['id']}", headers=user["headers"]).json()["download"]["url"]
    tick(frozen, 601)
    assert client.get(expiring).status_code == 404  # 만료

    other = client.get("/api/v1/data-exports/job_none", headers=user["headers"])
    assert other.status_code == 404 and other.json()["error"]["code"] == "EXPORT_NOT_FOUND"


def test_deletion_request_hides_then_deletes_after_grace(client, user, frozen):
    seed_data(user)
    bad = client.post(
        "/api/v1/data-deletion-requests",
        json={"scope": "ALL_CHILD_DATA", "confirmation": "지워줘"},
        headers=user["headers"],
    )
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_INPUT"

    created = client.post(
        "/api/v1/data-deletion-requests",
        json={"scope": "ALL_CHILD_DATA", "confirmation": "DELETE", "reason": "USER_REQUEST"},
        headers=user["headers"],
    )
    assert created.status_code == 202
    request = created.json()["request"]
    assert request["id"].startswith("del_") and request["status"] == "QUEUED"
    assert request["hiddenAt"] is not None and request["cancellable"] is True
    assert request["targetId"] == user["childId"]

    hidden = client.get("/api/v1/data/overview", headers=user["headers"]).json()
    assert hidden["hiddenScopes"] == ["ALL_CHILD_DATA"]
    assert hidden["pendingDeletionRequestId"] == request["id"]

    cancelled = client.post(
        f"/api/v1/data-deletion-requests/{request['id']}/cancel", headers=user["headers"]
    ).json()["request"]
    assert cancelled["status"] == "CANCELLED" and cancelled["hiddenAt"] is None
    assert client.get("/api/v1/data/overview", headers=user["headers"]).json()["counts"]["stories"] == 1

    again = client.post(
        f"/api/v1/data-deletion-requests/{request['id']}/cancel", headers=user["headers"]
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "DELETION_NOT_CANCELLABLE"

    second = client.post(
        "/api/v1/data-deletion-requests",
        json={"scope": "ALL_CHILD_DATA", "confirmation": "DELETE"},
        headers=user["headers"],
    ).json()["request"]
    tick(frozen, 8 * 24 * 3600)  # 유예기간(7일)을 넘긴다
    relogin(user)
    done = client.get(f"/api/v1/data-deletion-requests/{second['id']}", headers=user["headers"]).json()["request"]
    assert done["status"] == "SUCCEEDED" and done["completedAt"] is not None
    assert done["result"]["stories"] == 1 and done["result"]["messages"] == 2
    assert done["cancellable"] is False

    empty = client.get("/api/v1/data/overview", headers=user["headers"]).json()["counts"]
    assert empty["stories"] == 0 and empty["conversations"] == 0 and empty["words"] == 0


def test_deletion_audit_trail_keeps_no_child_text(client, user, frozen):
    seed_data(user)
    created = client.post(
        "/api/v1/data-deletion-requests",
        json={"scope": "ALL_CHILD_DATA", "confirmation": "DELETE"},
        headers=user["headers"],
    ).json()["request"]
    tick(frozen, 8 * 24 * 3600)
    relogin(user)
    client.get(f"/api/v1/data-deletion-requests/{created['id']}", headers=user["headers"])

    session = next(db.get_session())
    rows = list(session.query(DeletionRequest).all())
    audit = json.dumps(
        [{column.name: str(getattr(row, column.name)) for column in row.__table__.columns} for row in rows],
        ensure_ascii=False,
    )
    assert STORY_BODY not in audit and "차가운 컵 이야기" not in audit
    assert created["id"] in audit and user["id"] in audit and user["childId"] in audit
    notifications = json.dumps(
        [row.title + row.body + json.dumps(row.data) for row in session.query(Notification).all()], ensure_ascii=False
    )
    assert STORY_BODY not in notifications


def test_deletion_needs_recent_login(client, user, frozen):
    tick(frozen, 2000)  # 재인증 한도(900초)를 넘긴 오래된 토큰
    res = client.post(
        "/api/v1/data-deletion-requests",
        json={"scope": "ALL_CHILD_DATA", "confirmation": "DELETE"},
        headers=user["headers"],
    )
    assert res.status_code == 401 and res.json()["error"]["code"] == "REAUTH_REQUIRED"


def test_account_deletion_request_and_cancel(client, user, frozen):
    seed_data(user)
    created = client.post(
        "/api/v1/account-deletion-requests",
        json={"confirmation": "DELETE", "reason": "NO_LONGER_USED"},
        headers=user["headers"],
    )
    assert created.status_code == 202
    request = created.json()["request"]
    assert request["id"].startswith("acc_") and request["kind"] == "ACCOUNT"
    effective = clock.now() + timedelta(days=30)
    assert request["effectiveAt"] == effective.strftime("%Y-%m-%dT%H:%M:%SZ")

    fetched = client.get(f"/api/v1/account-deletion-requests/{request['id']}", headers=user["headers"]).json()
    assert fetched["request"]["status"] == "QUEUED"
    # 아이 데이터 삭제 요청과 계정 탈퇴 요청은 서로 다른 목록이다.
    assert client.get(f"/api/v1/data-deletion-requests/{request['id']}", headers=user["headers"]).status_code == 404

    cancelled = client.post(
        f"/api/v1/account-deletion-requests/{request['id']}/cancel", headers=user["headers"]
    ).json()["request"]
    assert cancelled["status"] == "CANCELLED"


def test_account_deletion_closes_account_after_grace(client, user, frozen):
    seed_data(user)
    request = client.post(
        "/api/v1/account-deletion-requests", json={"confirmation": "DELETE"}, headers=user["headers"]
    ).json()["request"]
    tick(frozen, 31 * 24 * 3600)
    relogin(user)
    done = client.get(f"/api/v1/account-deletion-requests/{request['id']}", headers=user["headers"]).json()["request"]
    assert done["status"] == "SUCCEEDED"
    assert done["result"]["stories"] == 1 and done["result"]["notifications"] >= 1
    # 토큰이 폐기돼 더는 쓸 수 없다.
    assert client.get("/api/v1/data/overview", headers=user["headers"]).status_code == 401


def test_ops_endpoints_need_a_token(client):
    anonymous = TestClient(app)
    for method, path in (
        ("post", "/api/v1/speech/stream-tickets"),
        ("get", "/api/v1/notifications"),
        ("get", "/api/v1/data/overview"),
    ):
        res = anonymous.request(method, path, json={})
        assert res.status_code == 401 and res.json()["error"]["code"] == "UNAUTHORIZED"


def test_voice_consent_from_consent_api_opens_speech(client, frozen):
    """예전 권한이 없어도 보호자가 음성 동의를 남기면 음성 API 가 열린다(명세 26절)."""
    from app.v1 import models_accounts

    session = next(db.get_session())
    account = create_account(session, is_tester=True, nickname="달")
    headers = auth(issue_access_token(session, account))
    session.commit()

    blocked = client.post("/api/v1/speech/stream-tickets", json={"locale": "ko-KR"}, headers=headers)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert blocked.json()["error"]["details"]["documentIds"] == ["voice_retention"]

    created = client.post("/api/v1/profiles", json={"nickname": "달"}, headers=headers)
    assert created.status_code == 201
    profile_id = created.json()["profile"]["id"]
    document = models_accounts.DOCUMENT_BY_ID["voice_retention"]
    granted = client.post(
        "/api/v1/consents",
        json={
            "profileId": profile_id,
            "items": [{"documentId": document.id, "version": document.version, "agreed": True}],
            "actor": "GUARDIAN",
        },
        headers=headers,
    )
    assert granted.status_code == 201
    opened = client.post("/api/v1/speech/stream-tickets", json={"locale": "ko-KR"}, headers=headers)
    assert opened.status_code == 201 and opened.json()["ticket"]
