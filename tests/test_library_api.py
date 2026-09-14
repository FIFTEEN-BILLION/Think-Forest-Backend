"""낱말 풀이·단어 보관함·퀴즈·보호자 단어 검사·이야기책 API."""

from __future__ import annotations

from conftest import make_child, make_family, talk_to_story

WORDS = [
    {"word": "수증기", "meaning": "물이 눈에 안 보이는 기체가 된 것"},
    {"word": "겉면", "meaning": "물건의 바깥쪽 면"},
    {"word": "활주로", "meaning": "비행기가 달리다가 뜨고 내리는 긴 길"},
]


def test_word_bank_explain_and_quiz_without_scores(client, family, child):
    saved = [client.post("/children/me/words", json=w, headers=child["headers"]) for w in WORDS]
    assert [r.status_code for r in saved] == [201, 201, 201]
    again = client.post("/children/me/words", json=WORDS[0], headers=child["headers"])
    assert again.status_code == 200 and again.json()["id"] == saved[0].json()["id"]

    explained = client.post("/words/explain", json={"text": "수증기가 컵 겉면에 붙어요"}, headers=child["headers"]).json()
    assert explained["ai"] is False and {w["word"] for w in explained["words"]} == {"수증기", "겉면"}

    quiz = client.post("/children/me/word-quizzes", json={"count": 2}, headers=child["headers"]).json()
    assert len(quiz["questions"]) == 2 and all(len(q["options"]) == 3 for q in quiz["questions"])
    assert "answer" not in str(quiz["questions"])
    meanings = {w["word"]: w["meaning"] for w in WORDS}
    for q in quiz["questions"]:
        word = q["prompt"].split("’")[0].lstrip("‘")
        chosen = q["options"].index(meanings[word])
        result = client.post(
            f"/children/me/word-quizzes/{quiz['id']}/answers", json={"index": q["index"], "chosen": chosen},
            headers=child["headers"],
        ).json()  # fmt: skip
        assert result["correct"] is True
    duplicate = client.post(
        f"/children/me/word-quizzes/{quiz['id']}/answers", json={"index": 0, "chosen": 0}, headers=child["headers"]
    )
    assert duplicate.status_code == 409

    test = client.post(f"/guardian/children/{child['id']}/word-tests", json={"count": 3}, headers=family["headers"]).json()
    pending = client.get("/children/me/word-quizzes?pending=true", headers=child["headers"]).json()
    assert [p["id"] for p in pending] == [test["id"]] and pending[0]["assignedBy"] == "guardian"
    client.post(
        f"/children/me/word-quizzes/{test['id']}/answers", json={"index": 0, "chosen": 0}, headers=child["headers"]
    )
    results = client.get(f"/guardian/children/{child['id']}/word-tests", headers=family["headers"]).json()
    assert len(results[0]["answers"]) == 1


def test_quiz_needs_saved_words(client, child):
    assert client.post("/children/me/word-quizzes", json={}, headers=child["headers"]).status_code == 409


def test_books_collect_own_stories(client, child, frozen):
    story = talk_to_story(client, child, frozen)["final"]["story"]
    book = client.post(
        "/children/me/books", json={"title": "나의 과학 이야기책", "storyIds": [story["id"]]}, headers=child["headers"]
    ).json()
    assert book["stories"][0]["id"] == story["id"]
    other = make_child(client, make_family(client))
    r = client.post("/children/me/books", json={"title": "훔친 책", "storyIds": [story["id"]]}, headers=other["headers"])
    assert r.status_code == 404
