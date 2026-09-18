"""v1 단어장·단어 퀴즈 — 대화에서 만난 낱말을 담고, 복습 퀴즈로 다시 만난다.

점수를 만들지 않는다. 퀴즈 결과는 낱말 상태(NEW·PRACTICING·FAMILIAR)와 다음 복습 시각만 바꾼다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import conversation_scope, cursor, idempotency, library_words
from ..deps import ProfileScope, require_profile
from ..errors import ApiError
from ..library_common import child_text, current_user
from ..library_schemas import (
    QuizAnswerRequest,
    QuizAnswerResponse,
    QuizAnswerResult,
    QuizCreateRequest,
    QuizProgress,
    WordbookCreateRequest,
    WordbookEntryResponse,
    WordbookList,
    WordbookPatchRequest,
    WordQuizOut,
    WordStatus,
)
from ..models_conversation import ConversationMessage, ConversationSession
from ..models_library import WordbookEntry, WordQuizQuestion, WordQuizV1

router = APIRouter(tags=["v1-wordbook"])


def _own_entry(db: Session, scope: ProfileScope, entry_id: str) -> WordbookEntry:
    entry = db.get(WordbookEntry, entry_id)
    if entry is None or entry.profile_id != scope.profile_id:
        raise ApiError(404, "WORDBOOK_ENTRY_NOT_FOUND", "단어장에서 찾을 수 없어요.")
    return entry


def _own_quiz(db: Session, scope: ProfileScope, quiz_id: str) -> WordQuizV1:
    quiz = db.get(WordQuizV1, quiz_id)
    if quiz is None or quiz.profile_id != scope.profile_id:
        raise ApiError(404, "QUIZ_NOT_FOUND", "퀴즈를 찾을 수 없어요.")
    return quiz


# --- 단어장 ----------------------------------------------------------------------


@router.get("/wordbook", response_model=WordbookList)
def list_wordbook(
    status: WordStatus | None = Query(default=None),
    query: str | None = Query(default=None, max_length=30),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    stmt = select(WordbookEntry).where(WordbookEntry.profile_id == scope.profile_id)
    if status:
        stmt = stmt.where(WordbookEntry.status == status)
    if query and query.strip():
        q = query.strip()
        stmt = stmt.where(
            or_(
                WordbookEntry.word.contains(q, autoescape=True),
                WordbookEntry.meaning.contains(q, autoescape=True),
            )
        )
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(
            or_(WordbookEntry.updated_at < at, and_(WordbookEntry.updated_at == at, WordbookEntry.id < row_id))
        )
    rows = list(db.scalars(stmt.order_by(WordbookEntry.updated_at.desc(), WordbookEntry.id.desc()).limit(size + 1)))
    page = rows[:size]
    return WordbookList(
        summary=library_words.summary(db, scope.profile_id),
        items=[library_words.entry_out(e) for e in page],
        next_cursor=cursor.encode(page[-1].updated_at, page[-1].id) if len(rows) > size else None,
    )


@router.post("/wordbook/entries", response_model=WordbookEntryResponse, status_code=201)
def add_entry(
    req: WordbookCreateRequest,
    response: Response,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    """대화에서 만난 낱말을 담는다. 뜻풀이는 AI 가 만들고, 못 쓰면 검수 사전이나 직접 적기로 넘어간다."""
    route = "POST /wordbook/entries"
    if (replayed := idempotency.replay(db, scope.user.id, idempotency_key, route)) is not None:
        return replayed
    word = child_text(db, current_user(scope), req.word, 30)
    message = db.get(ConversationMessage, req.message_id)
    session = db.get(ConversationSession, message.session_id) if message else None
    if (
        message is None
        or session is None
        or session.user_id != scope.profile.user_id
        or not conversation_scope.belongs(db, session.id, scope.child.id)
    ):
        raise ApiError(404, "MESSAGE_NOT_FOUND", "그 대화를 찾을 수 없어요.")
    if req.conversation_id and req.conversation_id != session.id:
        raise ApiError(404, "MESSAGE_NOT_FOUND", "그 대화를 찾을 수 없어요.")
    if word not in message.content:
        raise ApiError(400, "INVALID_INPUT", "그 대화에 없는 낱말이에요.", {"fields": ["word"]})

    existing = db.scalar(
        select(WordbookEntry).where(WordbookEntry.profile_id == scope.profile_id, WordbookEntry.word == word)
    )
    if existing is not None:
        response.status_code = 200
        return WordbookEntryResponse(entry=library_words.entry_out(existing))

    meaning, example, source = library_words.explain(db, scope, word, message.content, session)
    now = clock.now()
    entry = WordbookEntry(
        profile_id=scope.profile_id,
        user_id=scope.user.id,
        word=word,
        meaning=meaning,
        example=example,
        source=source,
        source_conversation_id=session.id,
        source_message_id=message.id,
        source_sentence=message.content,
        created_at=now,
        updated_at=now,
    )
    library_words.set_status(entry, "NEW", now)
    db.add(entry)
    db.flush()
    out = WordbookEntryResponse(entry=library_words.entry_out(entry))
    idempotency.remember(db, scope.user.id, idempotency_key, route, out, status_code=201)
    db.commit()
    return out


@router.get("/wordbook/entries/{entry_id}", response_model=WordbookEntryResponse)
def get_entry(entry_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)):
    return WordbookEntryResponse(entry=library_words.entry_out(_own_entry(db, scope, entry_id)))


@router.patch("/wordbook/entries/{entry_id}", response_model=WordbookEntryResponse)
def edit_entry(
    entry_id: str,
    req: WordbookPatchRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    entry = _own_entry(db, scope, entry_id)
    now = clock.now()
    if req.my_sentence is not None:
        entry.my_sentence = child_text(db, current_user(scope), req.my_sentence, 200)
        entry.updated_at = now
    if req.status is not None:
        library_words.set_status(entry, req.status, now)
    db.commit()
    return WordbookEntryResponse(entry=library_words.entry_out(entry))


@router.delete("/wordbook/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)):
    entry = _own_entry(db, scope, entry_id)
    for question in db.scalars(select(WordQuizQuestion).where(WordQuizQuestion.entry_id == entry.id)):
        db.delete(question)
    db.delete(entry)
    db.commit()
    return Response(status_code=204)


# --- 단어 퀴즈 --------------------------------------------------------------------


@router.post("/word-quizzes", response_model=WordQuizOut, status_code=201)
def create_quiz(
    req: QuizCreateRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    """내 단어장에 담긴 낱말로만 낸다. 복습할 때가 된 낱말이 먼저 나온다."""
    route = "POST /word-quizzes"
    if (replayed := idempotency.replay(db, scope.user.id, idempotency_key, route)) is not None:
        return replayed
    quiz = library_words.create_quiz(db, scope, req.mode, req.count, req.status)
    out = library_words.quiz_out(db, quiz)
    idempotency.remember(db, scope.user.id, idempotency_key, route, out, status_code=201)
    db.commit()
    return out


@router.post("/word-quizzes/{quiz_id}/answers", response_model=QuizAnswerResponse)
def answer_quiz(
    quiz_id: str,
    req: QuizAnswerRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    quiz = _own_quiz(db, scope, quiz_id)
    question, entry, correct = library_words.answer_question(db, quiz, req.question_id, req.option_id)
    progress = library_words.quiz_out(db, quiz)
    db.commit()
    return QuizAnswerResponse(
        result=QuizAnswerResult(question_id=question.id, correct=correct, correct_option_id=question.answer_option_id),
        entry=library_words.entry_out(entry),
        quiz=QuizProgress(
            id=quiz.id,
            status=quiz.status,  # type: ignore[arg-type]
            question_count=progress.question_count,
            answered_count=progress.answered_count,
        ),
    )


@router.get("/word-quizzes/{quiz_id}", response_model=WordQuizOut)
def get_quiz(quiz_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)):
    """새로고침 뒤 저장된 퀴즈와 답한 문제를 복원한다."""
    return library_words.quiz_out(db, _own_quiz(db, scope, quiz_id))
