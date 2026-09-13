"""낱말 풀이·단어 보관함·단어 퀴즈(아이 스스로 / 보호자 단어 검사)·이야기·이야기책."""

from __future__ import annotations

import random

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import ai_block_reason, guardian_child, require_child, require_guardian
from ..db import get_session
from ..models import Book, Child, Family, Story, Talk, Word, WordQuiz, new_id
from ..prompts import talk as prompt
from ..safety import pii
from ..safety import topics as sensitive
from ..schemas.library import (
    AnswerIn,
    AnswerOut,
    BookIn,
    BookOut,
    ExplainRequest,
    ExplainResponse,
    QuizCreateRequest,
    QuizOut,
    QuizQuestionOut,
    WordIn,
    WordOut,
)
from ..schemas.talk import StoryOut, WordExplainLLM, WordNote
from ..services import usage
from ..services.llm import LlmError, call_structured
from ..talks import topics as bank
from ..talks.planner import clip
from ..talks.progress import LEARNED_CORRECT
from .talks import story_out

router = APIRouter(tags=["library"])

OPTIONS_PER_QUESTION = 3


# --- 낱말 풀이 ---------------------------------------------------------------


@router.post("/words/explain", response_model=ExplainResponse)
def explain_words(
    req: ExplainRequest, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> ExplainResponse:
    """호버·탭으로 보여 줄 어려운 낱말 풀이. AI 를 못 쓰면 검수된 주제 사전에서만 찾는다."""
    glossary = [WordNote(word=w, meaning=m) for w, m in bank.GLOSSARY.items() if w in req.text][:5]
    if sensitive.detect(req.text):
        return ExplainResponse(ai=False, error="blocked", words=[])
    error = ai_block_reason(child) or (None if usage.try_consume(child.id) else "daily_limit")
    if error:
        return ExplainResponse(ai=False, error=error, words=glossary)
    masked = pii.mask(req.text).text
    try:
        out = call_structured(
            purpose="words.explain",
            instructions=prompt.words_instructions(),
            user_input=prompt.words_input(masked),
            schema=WordExplainLLM,
        )
    except LlmError as exc:
        return ExplainResponse(ai=False, error=f"ai_failed:{exc.code}", words=glossary)
    words = [
        WordNote(word=clip(w.word, 30), meaning=clip(w.meaning, 80), example=clip(w.example, 100))
        for w in out.words[:5]
        if w.word and w.word in masked and not sensitive.detect(f"{w.meaning} {w.example}")
    ]
    return ExplainResponse(ai=True, words=words)


# --- 단어 보관함 --------------------------------------------------------------


def word_out(word: Word) -> WordOut:
    return WordOut(
        id=word.id,
        word=word.word,
        meaning=word.meaning,
        example=word.example,
        quiz_seen=word.quiz_seen,
        quiz_correct=word.quiz_correct,
        learned=word.quiz_correct >= LEARNED_CORRECT,
        created_at=word.created_at,
    )


def _words(db: Session, child: Child) -> list[Word]:
    return list(db.scalars(select(Word).where(Word.child_id == child.id).order_by(Word.created_at)))


@router.get("/children/me/words", response_model=list[WordOut])
def list_words(child: Child = Depends(require_child), db: Session = Depends(get_session)) -> list[WordOut]:
    return [word_out(w) for w in _words(db, child)]


@router.post("/children/me/words", response_model=WordOut, status_code=201)
def save_word(
    req: WordIn, response: Response, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> WordOut:
    word = req.word.strip()
    if sensitive.detect(f"{word} {req.meaning} {req.example}"):
        raise HTTPException(status_code=422, detail="unsafe_word")
    existing = db.scalar(select(Word).where(Word.child_id == child.id, Word.word == word))
    if existing:
        response.status_code = 200
        return word_out(existing)
    talk = db.get(Talk, req.talk_id) if req.talk_id else None
    saved = Word(
        child_id=child.id,
        word=word,
        meaning=req.meaning.strip(),
        example=req.example.strip(),
        talk_id=talk.id if talk and talk.child_id == child.id else None,
    )
    db.add(saved)
    db.commit()
    return word_out(saved)


@router.delete("/children/me/words/{word_id}", status_code=204)
def delete_word(word_id: str, child: Child = Depends(require_child), db: Session = Depends(get_session)) -> Response:
    word = db.get(Word, word_id)
    if word is None or word.child_id != child.id:
        raise HTTPException(status_code=404, detail="word_not_found")
    db.delete(word)
    db.commit()
    return Response(status_code=204)


# --- 단어 퀴즈 · 보호자 단어 검사 ------------------------------------------------


def quiz_out(quiz: WordQuiz) -> QuizOut:
    return QuizOut(
        id=quiz.id,
        assigned_by=quiz.assigned_by,  # type: ignore[arg-type]
        questions=[
            QuizQuestionOut(index=q["index"], word_id=q["wordId"], prompt=q["prompt"], options=q["options"])
            for q in quiz.questions
        ],
        answers=[AnswerOut(**a) for a in quiz.answers],
        completed=quiz.completed_at is not None,
        created_at=quiz.created_at,
    )


def create_quiz(db: Session, child: Child, assigned_by: str, count: int, word_ids: list[str] | None) -> WordQuiz:
    query = select(Word).where(Word.child_id == child.id)
    if word_ids:
        query = query.where(Word.id.in_(word_ids))
    words = list(db.scalars(query.order_by(Word.created_at)))
    if not words:
        raise HTTPException(status_code=409, detail="need_saved_words")
    quiz = WordQuiz(id=new_id(), child_id=child.id, assigned_by=assigned_by, answers=[])
    rng = random.Random(quiz.id)
    pool = list(dict.fromkeys([w.meaning for w in words] + list(bank.GLOSSARY.values())))
    questions = []
    for index, word in enumerate(rng.sample(words, k=min(count, len(words)))):
        distractors = [m for m in pool if m != word.meaning]
        rng.shuffle(distractors)
        options = [word.meaning, *distractors[: OPTIONS_PER_QUESTION - 1]]
        rng.shuffle(options)
        questions.append(
            {
                "index": index,
                "wordId": word.id,
                "word": word.word,
                "prompt": f"‘{word.word}’의 뜻은 무엇일까?",
                "options": options,
                "answer": options.index(word.meaning),  # 응답에 내보내지 않는다
            }
        )
    quiz.questions = questions
    db.add(quiz)
    db.commit()
    return quiz


@router.post("/children/me/word-quizzes", response_model=QuizOut, status_code=201)
def new_quiz(
    req: QuizCreateRequest, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> QuizOut:
    return quiz_out(create_quiz(db, child, "child", req.count, req.word_ids))


@router.get("/children/me/word-quizzes", response_model=list[QuizOut])
def list_quizzes(
    pending: bool = False, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> list[QuizOut]:
    query = select(WordQuiz).where(WordQuiz.child_id == child.id).order_by(WordQuiz.created_at.desc())
    quizzes = [q for q in db.scalars(query) if not pending or q.completed_at is None]
    return [quiz_out(q) for q in quizzes]


@router.post("/children/me/word-quizzes/{quiz_id}/answers", response_model=AnswerOut)
def answer_quiz(
    quiz_id: str, req: AnswerIn, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> AnswerOut:
    quiz = db.get(WordQuiz, quiz_id)
    if quiz is None or quiz.child_id != child.id:
        raise HTTPException(status_code=404, detail="quiz_not_found")
    if req.index >= len(quiz.questions):
        raise HTTPException(status_code=422, detail="invalid_question")
    if any(a["index"] == req.index for a in quiz.answers):
        raise HTTPException(status_code=409, detail="already_answered")
    question = quiz.questions[req.index]
    if req.chosen >= len(question["options"]):
        raise HTTPException(status_code=422, detail="invalid_option")
    correct = req.chosen == question["answer"]
    answer = {"index": req.index, "chosen": req.chosen, "correct": correct, "word": question["word"]}
    quiz.answers = [*quiz.answers, answer]
    word = db.get(Word, question["wordId"])
    if word:
        word.quiz_seen += 1
        word.quiz_correct += int(correct)
    if len(quiz.answers) == len(quiz.questions):
        quiz.completed_at = clock.now()
    db.commit()
    return AnswerOut(**answer)


@router.post("/guardian/children/{child_id}/word-tests", response_model=QuizOut, status_code=201)
def assign_word_test(
    child_id: str,
    req: QuizCreateRequest,
    family: Family = Depends(require_guardian),
    db: Session = Depends(get_session),
) -> QuizOut:
    child = guardian_child(child_id, family, db)
    return quiz_out(create_quiz(db, child, "guardian", req.count, req.word_ids))


@router.get("/guardian/children/{child_id}/word-tests", response_model=list[QuizOut])
def word_test_results(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> list[QuizOut]:
    child = guardian_child(child_id, family, db)
    query = select(WordQuiz).where(WordQuiz.child_id == child.id, WordQuiz.assigned_by == "guardian")
    return [quiz_out(q) for q in db.scalars(query.order_by(WordQuiz.created_at.desc()))]


@router.get("/guardian/children/{child_id}/words", response_model=list[WordOut])
def guardian_words(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> list[WordOut]:
    return [word_out(w) for w in _words(db, guardian_child(child_id, family, db))]


# --- 이야기 · 이야기책 --------------------------------------------------------


def _own_story(db: Session, child: Child, story_id: str) -> Story:
    story = db.get(Story, story_id)
    if story is None or story.child_id != child.id:
        raise HTTPException(status_code=404, detail="story_not_found")
    return story


def book_out(db: Session, book: Book) -> BookOut:
    stories = [db.get(Story, sid) for sid in book.story_ids]
    return BookOut(
        id=book.id, title=book.title, stories=[story_out(s) for s in stories if s], created_at=book.created_at
    )


@router.get("/children/me/stories", response_model=list[StoryOut])
def list_stories(child: Child = Depends(require_child), db: Session = Depends(get_session)) -> list[StoryOut]:
    stories = db.scalars(select(Story).where(Story.child_id == child.id).order_by(Story.created_at.desc()))
    return [story_out(s) for s in stories]


@router.get("/children/me/stories/{story_id}", response_model=StoryOut)
def get_story(story_id: str, child: Child = Depends(require_child), db: Session = Depends(get_session)) -> StoryOut:
    return story_out(_own_story(db, child, story_id))


@router.post("/children/me/books", response_model=BookOut, status_code=201)
def create_book(req: BookIn, child: Child = Depends(require_child), db: Session = Depends(get_session)) -> BookOut:
    if len(set(req.story_ids)) != len(req.story_ids):
        raise HTTPException(status_code=422, detail="duplicate_story")
    stories = [_own_story(db, child, sid) for sid in req.story_ids]
    title = " ".join(req.title.split())
    if sensitive.detect(title):
        raise HTTPException(status_code=422, detail="unsafe_title")
    book = Book(child_id=child.id, title=title, story_ids=[s.id for s in stories])
    db.add(book)
    db.commit()
    return book_out(db, book)


@router.get("/children/me/books", response_model=list[BookOut])
def list_books(child: Child = Depends(require_child), db: Session = Depends(get_session)) -> list[BookOut]:
    books = db.scalars(select(Book).where(Book.child_id == child.id).order_by(Book.created_at.desc()))
    return [book_out(db, b) for b in books]


@router.get("/children/me/books/{book_id}", response_model=BookOut)
def get_book(book_id: str, child: Child = Depends(require_child), db: Session = Depends(get_session)) -> BookOut:
    book = db.get(Book, book_id)
    if book is None or book.child_id != child.id:
        raise HTTPException(status_code=404, detail="book_not_found")
    return book_out(db, book)
