"""v1 책장 — 이야기 목록·고쳐 쓰기·지우기, 단어장, 단어 퀴즈, 이야기책, 소유권."""

from __future__ import annotations

import json
from datetime import timedelta

from app import db
from app.schemas.talk import LlmWord, WordExplainLLM
from app.v1 import ai_gate
from app.v1.cursor import iso
from app.v1.library_schemas import BookIntroLLM
from app.v1.models_conversation import ChildProfile, StoryRecord
from app.v1.models_library import WordbookEntry
from sqlalchemy import select

from conftest import tick
from test_v1_conversations import make_user, ready_story, say

WORDBOOK = "/api/v1/wordbook"
ENTRIES = f"{WORDBOOK}/entries"
QUIZZES = "/api/v1/word-quizzes"
BOOKS = "/api/v1/books"
STORIES = "/api/v1/stories"

VAPOR = "컵 밖에 수증기가 모여서 물방울이 된 것 같아."
RUNWAY = "비행기는 활주로에서 빠르게 달리다가 날아올라."


# --- 도우미 ----------------------------------------------------------------------


def make_profile_user(nickname: str = "별") -> dict:
    """첫인사를 마친 아이. 책장·단어장은 아이 프로필 단위로 갈라 둔다."""
    user = make_user(nickname=nickname)
    session = next(db.get_session())
    profile = ChildProfile(child_id=user["child_id"], user_id=user["id"], nickname=nickname)
    session.add(profile)
    session.commit()
    user["profileId"] = profile.id
    return user


def complete_story(client, user: dict, frozen: dict, topic_id: str = "topic_ice_cup") -> dict:
    conversation = ready_story(client, user, frozen, topic_id)
    res = client.post(f"/api/v1/conversations/{conversation['id']}/complete", json={}, headers=user["headers"])
    assert res.status_code == 200, res.text
    return res.json()["story"]


def message_id(client, user: dict, conversation: dict, sentence: str, word: str) -> str:
    """대화에 문장을 하나 넣고, 그 낱말이 담긴 메시지 id 를 돌려준다."""
    assert say(client, user, conversation, text=sentence).status_code == 200
    detail = client.get(f"/api/v1/conversations/{conversation['id']}", headers=user["headers"]).json()
    return next(m["id"] for m in reversed(detail["messages"]) if word in m["content"])


def save_word(client, user: dict, conversation: dict, sentence: str, word: str, **kwargs):
    body = {"word": word, "messageId": message_id(client, user, conversation, sentence, word)}
    return client.post(ENTRIES, json=body, headers=user["headers"], **kwargs)


def explain_ai(monkeypatch, word: str, meaning: str, example: str = "") -> list[dict]:
    """낱말 뜻풀이·머리말만 AI 로 바꿔 끼운다. 대화 엔진 호출은 실패시켜 규칙 대사로 이어 가게 둔다."""
    calls: list[dict] = []
    monkeypatch.setattr(ai_gate, "ai_block_reason", lambda child: None)

    def fake(**kwargs):
        if kwargs["schema"] is WordExplainLLM:
            calls.append(kwargs)
            return WordExplainLLM(words=[LlmWord(word=word, meaning=meaning, example=example)])
        if kwargs["schema"] is BookIntroLLM:
            calls.append(kwargs)
            return BookIntroLLM(introduction="생각이 자란 이야기를 모았어요. 한 편씩 읽어 볼까요?")
        raise ai_gate.LlmError("not_mocked", "이 테스트에서는 대화 AI 를 쓰지 않는다")

    monkeypatch.setattr(ai_gate, "call_structured", fake)
    return calls


# --- 이야기 목록 · 고쳐 쓰기 · 지우기 ------------------------------------------------


def test_story_filters_and_cursor(client, frozen):
    user = make_profile_user()
    snow = complete_story(client, user, frozen, "topic_snow")
    tick(frozen, 60)
    ice = complete_story(client, user, frozen, "topic_ice_cup")

    listed = client.get(STORIES, headers=user["headers"]).json()
    assert [s["id"] for s in listed["items"]] == [ice["id"], snow["id"]]
    page1 = client.get(f"{STORIES}?limit=1", headers=user["headers"]).json()
    page2 = client.get(f"{STORIES}?limit=1&cursor={page1['nextCursor']}", headers=user["headers"]).json()
    assert [page1["items"][0]["id"], page2["items"][0]["id"]] == [ice["id"], snow["id"]]
    assert page2["nextCursor"] is None
    assert [s["id"] for s in client.get(f"{STORIES}?query=하얀 눈", headers=user["headers"]).json()["items"]] == [
        snow["id"]
    ]
    assert len(client.get(f"{STORIES}?category=SCIENCE", headers=user["headers"]).json()["items"]) == 2
    assert client.get(f"{STORIES}?category=MATH", headers=user["headers"]).json()["items"] == []
    assert client.get(f"{STORIES}?to=2026-09-13", headers=user["headers"]).json()["items"] == []
    assert len(client.get(f"{STORIES}?from=2026-09-14", headers=user["headers"]).json()["items"]) == 2

    favorite = client.put(f"{STORIES}/{snow['id']}/favorite", headers=user["headers"]).json()["story"]
    assert favorite["favorite"] is True and favorite["version"] == 1
    assert [s["id"] for s in client.get(f"{STORIES}?favorite=true", headers=user["headers"]).json()["items"]] == [
        snow["id"]
    ]
    assert client.delete(f"{STORIES}/{snow['id']}/favorite", headers=user["headers"]).json()["story"]["favorite"] is False


def test_story_patch_needs_matching_version_and_keeps_ai_original(client, frozen):
    user = make_profile_user()
    story = complete_story(client, user, frozen)
    with next(db.get_session()) as session:  # AI 정리본 원본을 두고 시작한다
        session.get(StoryRecord, story["id"]).ai_original = {"title": "AI 가 붙인 제목"}
        session.commit()

    assert client.patch(f"{STORIES}/{story['id']}", json={"title": "새 제목"}, headers=user["headers"]).status_code == 400
    stale = client.patch(
        f"{STORIES}/{story['id']}", json={"title": "새 제목"}, headers={**user["headers"], "If-Match": '"7"'}
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert stale.json()["error"]["details"]["currentVersion"] == 1

    edited = client.patch(
        f"{STORIES}/{story['id']}",
        json={"title": "차가운 컵의 물방울", "thoughtJourney": {"initialIdea": "컵에서 물이 샌다고 생각했어."}},
        headers={**user["headers"], "If-Match": '"1"'},
    )
    assert edited.status_code == 200
    body = edited.json()
    assert body["edited"] is True and body["story"]["version"] == 2
    assert body["story"]["title"] == "차가운 컵의 물방울"
    assert body["story"]["thoughtJourney"]["initialIdea"] == "컵에서 물이 샌다고 생각했어."
    assert body["story"]["thoughtJourney"]["evidence"] == story["thoughtJourney"]["evidence"]

    again = client.patch(f"{STORIES}/{story['id']}", json={"title": "또 고치기", "version": 1}, headers=user["headers"])
    assert again.status_code == 409
    assert client.patch(f"{STORIES}/{story['id']}", json={"title": "또 고치기", "version": 2}, headers=user["headers"]).status_code == 200  # noqa: E501
    with next(db.get_session()) as session:
        saved = session.get(StoryRecord, story["id"])
        assert saved.ai_original == {"title": "AI 가 붙인 제목"} and saved.version == 3


def test_story_detail_shows_saved_words_and_source_conversation(client, frozen, monkeypatch):
    user = make_profile_user()
    conversation = ready_story(client, user, frozen)
    explain_ai(monkeypatch, "수증기", "물이 눈에 안 보이는 기체가 된 것", "냄비에서 수증기가 올라와.")
    assert save_word(client, user, conversation, VAPOR, "수증기").status_code == 201
    res = client.post(f"/api/v1/conversations/{conversation['id']}/complete", json={}, headers=user["headers"])
    story = res.json()["story"]

    detail = client.get(f"{STORIES}/{story['id']}", headers=user["headers"]).json()
    assert detail["story"]["id"] == story["id"]
    assert [w["word"] for w in detail["wordsUsed"]] == ["수증기"]
    assert detail["wordsUsed"][0]["status"] == "NEW"
    source = detail["sourceConversation"]
    assert source["conversationId"] == conversation["id"] and source["status"] == "COMPLETED"
    assert source["topic"]["id"] == "topic_ice_cup" and source["messageCount"] > 0
    assert source["completedAt"] is not None


def test_story_delete_leaves_other_stories_and_books(client, frozen):
    user = make_profile_user()
    first = complete_story(client, user, frozen, "topic_snow")
    tick(frozen, 60)
    second = complete_story(client, user, frozen, "topic_ice_cup")
    book = client.post(
        BOOKS, json={"title": "내 이야기책", "storyIds": [first["id"], second["id"]]}, headers=user["headers"]
    ).json()["book"]

    assert client.delete(f"{STORIES}/{first['id']}", headers=user["headers"]).status_code == 204
    assert client.get(f"{STORIES}/{first['id']}", headers=user["headers"]).status_code == 404
    assert [s["id"] for s in client.get(STORIES, headers=user["headers"]).json()["items"]] == [second["id"]]
    kept = client.get(f"{BOOKS}/{book['id']}", headers=user["headers"]).json()["book"]
    assert [s["id"] for s in kept["stories"]] == [second["id"]] and kept["storyCount"] == 1


# --- 단어장 ----------------------------------------------------------------------


def test_wordbook_add_with_ai_then_fallback_and_summary(client, frozen, monkeypatch):
    user = make_profile_user()
    conversation = ready_story(client, user, frozen)
    fallback = save_word(client, user, conversation, RUNWAY, "활주로")
    assert fallback.status_code == 201
    entry = fallback.json()["entry"]
    assert entry["meaningSource"] == "fallback" and entry["status"] == "NEW"
    assert entry["meaning"] == "비행기가 달리다가 뜨고 내리는 긴 길"  # 검수 사전
    assert entry["source"]["conversationId"] == conversation["id"]
    assert entry["nextReviewAt"] == iso(frozen["now"] + timedelta(days=1))  # NEW 는 하루 뒤

    tick(frozen, 60)
    calls = explain_ai(monkeypatch, "수증기", "물이 눈에 안 보이는 기체가 된 것", "냄비에서 수증기가 올라와.")
    explained = save_word(client, user, conversation, VAPOR, "수증기").json()["entry"]
    assert explained["meaningSource"] == "ai" and explained["meaning"] == "물이 눈에 안 보이는 기체가 된 것"
    assert explained["example"] == "냄비에서 수증기가 올라와." and [c["purpose"] for c in calls] == ["words.explain"]

    again = save_word(client, user, conversation, VAPOR, "수증기")
    assert again.status_code == 200 and again.json()["entry"]["id"] == explained["id"]
    key = {**user["headers"], "Idempotency-Key": "word-1"}
    first = client.post(ENTRIES, json={"word": "활주로", "messageId": entry["source"]["messageId"]}, headers=key)
    assert client.post(ENTRIES, json={"word": "활주로", "messageId": entry["source"]["messageId"]}, headers=key).json() == first.json()  # noqa: E501

    listed = client.get(WORDBOOK, headers=user["headers"]).json()
    assert listed["summary"] == {"total": 2, "familiar": 0, "practicing": 0, "newThisWeek": 2, "new": 2, "dueForReview": 0}
    assert [e["word"] for e in listed["items"]] == ["수증기", "활주로"] and listed["nextCursor"] is None
    page = client.get(f"{WORDBOOK}?limit=1", headers=user["headers"]).json()
    assert len(page["items"]) == 1 and page["nextCursor"]
    assert [e["word"] for e in client.get(f"{WORDBOOK}?query=활주", headers=user["headers"]).json()["items"]] == ["활주로"]

    missing = client.post(ENTRIES, json={"word": "고래", "messageId": entry["source"]["messageId"]}, headers=user["headers"])
    assert missing.status_code == 400 and missing.json()["error"]["details"]["fields"] == ["word"]
    assert client.post(ENTRIES, json={"word": "고래", "messageId": "msg_none"}, headers=user["headers"]).status_code == 404


def test_wordbook_patch_status_and_delete(client, frozen):
    user = make_profile_user()
    conversation = ready_story(client, user, frozen)
    entry = save_word(client, user, conversation, RUNWAY, "활주로").json()["entry"]

    patched = client.patch(
        f"{ENTRIES}/{entry['id']}",
        json={"status": "FAMILIAR", "mySentence": "활주로에 비행기가 서 있었다."},
        headers=user["headers"],
    ).json()["entry"]
    assert patched["status"] == "FAMILIAR" and patched["mySentence"] == "활주로에 비행기가 서 있었다."
    assert patched["nextReviewAt"] == iso(frozen["now"] + timedelta(days=7))  # FAMILIAR 는 7일 뒤
    summary = client.get(WORDBOOK, headers=user["headers"]).json()["summary"]
    assert summary == {"total": 1, "familiar": 1, "practicing": 0, "newThisWeek": 1, "new": 0, "dueForReview": 0}
    assert [e["word"] for e in client.get(f"{WORDBOOK}?status=NEW", headers=user["headers"]).json()["items"]] == []

    assert client.get(f"{ENTRIES}/{entry['id']}", headers=user["headers"]).json()["entry"]["id"] == entry["id"]
    assert client.delete(f"{ENTRIES}/{entry['id']}", headers=user["headers"]).status_code == 204
    assert client.get(f"{ENTRIES}/{entry['id']}", headers=user["headers"]).status_code == 404
    assert client.get(WORDBOOK, headers=user["headers"]).json()["items"] == []


# --- 단어 퀴즈 --------------------------------------------------------------------


def test_quiz_uses_only_my_words_and_answer_moves_status_without_score(client, frozen):
    user = make_profile_user()
    other = make_profile_user(nickname="달")
    conversation = ready_story(client, user, frozen)
    save_word(client, user, conversation, RUNWAY, "활주로")
    save_word(client, user, conversation, VAPOR, "수증기")

    empty = client.post(QUIZZES, json={"count": 2}, headers=other["headers"])
    assert empty.status_code == 409 and empty.json()["error"]["code"] == "NO_WORDS_TO_QUIZ"

    quiz = client.post(QUIZZES, json={"count": 2, "mode": "MEANING_TO_WORD"}, headers=user["headers"])
    assert quiz.status_code == 201
    body = quiz.json()
    assert body["questionCount"] == 2 and body["answeredCount"] == 0 and body["status"] == "IN_PROGRESS"
    assert all(len(q["options"]) == 4 for q in body["questions"])
    assert "score" not in json.dumps(body).lower() and "answerOptionId" not in json.dumps(body)
    with next(db.get_session()) as session:
        rows = session.scalars(select(WordbookEntry).where(WordbookEntry.user_id == user["id"]))
        mine = {e.word: e.meaning for e in rows}
    assert all(any(m in q["prompt"] for m in mine.values()) for q in body["questions"])

    question = body["questions"][0]
    word = next(w for w, m in mine.items() if m in question["prompt"])
    correct = next(o["id"] for o in question["options"] if o["label"] == word)
    answered = client.post(
        f"{QUIZZES}/{body['id']}/answers",
        json={"questionId": question["id"], "optionId": correct},
        headers=user["headers"],
    )
    assert answered.status_code == 200
    result = answered.json()
    assert result["result"] == {"questionId": question["id"], "correct": True, "correctOptionId": correct}
    assert result["entry"]["word"] == word and result["entry"]["status"] == "PRACTICING"
    assert result["entry"]["nextReviewAt"] == iso(frozen["now"] + timedelta(days=3))  # PRACTICING 은 3일 뒤
    assert result["entry"]["lastReviewedAt"] == iso(frozen["now"])
    assert result["quiz"] == {"id": body["id"], "status": "IN_PROGRESS", "questionCount": 2, "answeredCount": 1}
    assert "score" not in json.dumps(result).lower()

    repeat = client.post(
        f"{QUIZZES}/{body['id']}/answers",
        json={"questionId": question["id"], "optionId": correct},
        headers=user["headers"],
    )
    assert repeat.status_code == 409 and repeat.json()["error"]["code"] == "ALREADY_ANSWERED"

    last = body["questions"][1]
    wrong = next(o["id"] for o in last["options"] if o["label"] not in mine)
    closed = client.post(
        f"{QUIZZES}/{body['id']}/answers", json={"questionId": last["id"], "optionId": wrong}, headers=user["headers"]
    ).json()
    assert closed["result"]["correct"] is False and closed["entry"]["status"] == "NEW"
    assert closed["quiz"]["status"] == "COMPLETED" and closed["quiz"]["answeredCount"] == 2


# --- 이야기책 --------------------------------------------------------------------


def test_book_create_reorder_add_remove_complete_and_delete(client, frozen, monkeypatch):
    user = make_profile_user()
    snow = complete_story(client, user, frozen, "topic_snow")
    tick(frozen, 60)
    ice = complete_story(client, user, frozen, "topic_ice_cup")
    tick(frozen, 60)
    pizza = complete_story(client, user, frozen, "topic_pizza_share")

    created = client.post(
        BOOKS,
        json={"title": "나의 과학 이야기책", "storyIds": [snow["id"], ice["id"]], "generateIntroduction": True},
        headers=user["headers"],
    )
    assert created.status_code == 201
    book = created.json()["book"]
    assert book["introductionSource"] == "fallback" and snow["title"] in book["introduction"]
    assert [s["id"] for s in book["stories"]] == [snow["id"], ice["id"]] and book["status"] == "DRAFT"
    assert book["storyCount"] == 2 and book["version"] == 1

    reordered = client.patch(
        f"{BOOKS}/{book['id']}",
        json={"storyIds": [ice["id"], snow["id"]], "title": "내가 만든 과학책", "version": 1},
        headers=user["headers"],
    ).json()["book"]
    assert [s["id"] for s in reordered["stories"]] == [ice["id"], snow["id"]]
    assert reordered["title"] == "내가 만든 과학책" and reordered["version"] == 2
    stale = client.patch(f"{BOOKS}/{book['id']}", json={"title": "옛 판", "version": 1}, headers=user["headers"])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"

    added = client.post(
        f"{BOOKS}/{book['id']}/stories", json={"storyId": pizza["id"], "position": 0}, headers=user["headers"]
    )
    assert added.status_code == 201
    assert [s["id"] for s in added.json()["book"]["stories"]] == [pizza["id"], ice["id"], snow["id"]]
    twice = client.post(f"{BOOKS}/{book['id']}/stories", json={"storyId": pizza["id"]}, headers=user["headers"])
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "STORY_ALREADY_IN_BOOK"

    removed = client.delete(f"{BOOKS}/{book['id']}/stories/{snow['id']}", headers=user["headers"]).json()["book"]
    assert [s["id"] for s in removed["stories"]] == [pizza["id"], ice["id"]]
    assert client.delete(f"{BOOKS}/{book['id']}/stories/{snow['id']}", headers=user["headers"]).status_code == 404

    done = client.post(f"{BOOKS}/{book['id']}/complete", headers=user["headers"]).json()["book"]
    assert done["status"] == "COMPLETED" and done["completedAt"]
    assert client.post(f"{BOOKS}/{book['id']}/complete", headers=user["headers"]).json()["book"] == done
    locked = client.patch(f"{BOOKS}/{book['id']}", json={"title": "고치기", "version": done["version"]}, headers=user["headers"])  # noqa: E501
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "BOOK_COMPLETED"

    listed = client.get(BOOKS, headers=user["headers"]).json()
    assert [b["id"] for b in listed["items"]] == [book["id"]] and listed["items"][0]["storyCount"] == 2
    assert client.get(f"{BOOKS}?status=DRAFT", headers=user["headers"]).json()["items"] == []
    assert client.delete(f"{BOOKS}/{book['id']}", headers=user["headers"]).status_code == 204
    assert client.get(f"{BOOKS}/{book['id']}", headers=user["headers"]).status_code == 404
    assert len(client.get(STORIES, headers=user["headers"]).json()["items"]) == 3  # 책을 지워도 이야기는 남는다

    explain_ai(monkeypatch, "수증기", "뜻")
    with_ai = client.post(
        BOOKS, json={"title": "티키가 쓴 머리말", "storyIds": [ice["id"]], "generateIntroduction": True}, headers=user["headers"]
    ).json()["book"]
    assert with_ai["introductionSource"] == "ai"
    assert with_ai["introduction"] == "생각이 자란 이야기를 모았어요. 한 편씩 읽어 볼까요?"


def test_empty_book_cannot_be_completed(client, frozen):
    user = make_profile_user()
    book = client.post(BOOKS, json={"title": "빈 책"}, headers=user["headers"]).json()["book"]
    res = client.post(f"{BOOKS}/{book['id']}/complete", headers=user["headers"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "BOOK_EMPTY"


# --- 소유권 ----------------------------------------------------------------------


def test_other_profile_gets_404_everywhere(client, frozen):
    owner, other = make_profile_user(), make_profile_user(nickname="달")
    conversation = ready_story(client, owner, frozen)
    entry = save_word(client, owner, conversation, RUNWAY, "활주로").json()["entry"]
    quiz = client.post(QUIZZES, json={"count": 1}, headers=owner["headers"]).json()
    story = complete_story(client, owner, frozen)
    book = client.post(BOOKS, json={"title": "내 책", "storyIds": [story["id"]]}, headers=owner["headers"]).json()["book"]

    for method, path, code in (
        ("GET", f"{ENTRIES}/{entry['id']}", "WORDBOOK_ENTRY_NOT_FOUND"),
        ("PATCH", f"{ENTRIES}/{entry['id']}", "WORDBOOK_ENTRY_NOT_FOUND"),
        ("DELETE", f"{ENTRIES}/{entry['id']}", "WORDBOOK_ENTRY_NOT_FOUND"),
        ("GET", f"{BOOKS}/{book['id']}", "BOOK_NOT_FOUND"),
        ("PATCH", f"{BOOKS}/{book['id']}", "BOOK_NOT_FOUND"),
        ("DELETE", f"{BOOKS}/{book['id']}", "BOOK_NOT_FOUND"),
        ("GET", f"{STORIES}/{story['id']}", "STORY_NOT_FOUND"),
        ("PATCH", f"{STORIES}/{story['id']}", "STORY_NOT_FOUND"),
        ("DELETE", f"{STORIES}/{story['id']}", "STORY_NOT_FOUND"),
    ):
        res = client.request(method, path, json={"status": "NEW", "title": "뺏기", "version": 1}, headers=other["headers"])
        assert res.status_code == 404, (method, path, res.text)
        assert res.json()["error"]["code"] == code, (method, path)

    stolen = client.post(f"{QUIZZES}/{quiz['id']}/answers", json={"questionId": quiz["questions"][0]["id"], "optionId": "A"}, headers=other["headers"])  # noqa: E501
    assert stolen.status_code == 404 and stolen.json()["error"]["code"] == "QUIZ_NOT_FOUND"
    taken = client.post(BOOKS, json={"title": "훔친 책", "storyIds": [story["id"]]}, headers=other["headers"])
    assert taken.status_code == 404 and taken.json()["error"]["code"] == "STORY_NOT_FOUND"
    assert client.get(WORDBOOK, headers=other["headers"]).json()["summary"]["total"] == 0
    assert client.get(BOOKS, headers=other["headers"]).json()["items"] == []
