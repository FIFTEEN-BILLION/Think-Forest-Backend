"""v1 단어장·단어 퀴즈 규칙 — 뜻풀이(AI·검수 사전), 복습 간격, 퀴즈 만들기·채점.

- 점수를 만들지 않는다. 답을 맞히면 낱말 상태가 한 칸 오르고 다음 복습이 멀어질 뿐이다.
- AI 뜻풀이가 막히면(ZDR 전·한도 초과·호출 실패) 검수 사전이나 "직접 적어 보기" 문장으로 이어 간다.
- 보기(선택지)는 퀴즈 id 를 씨앗으로 만든다. 같은 퀴즈는 언제 불러도 같은 보기가 나온다.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from ..prompts import talk as prompt
from ..schemas.talk import WordExplainLLM
from ..talks import topics as bank
from ..talks.planner import clip
from . import ai_gate
from .cursor import iso
from .deps import ProfileScope
from .errors import ApiError
from .library_common import safe_ai_line
from .library_schemas import (
    QuizOption,
    QuizQuestionOut,
    WordbookEntryOut,
    WordbookSummary,
    WordQuizOut,
)
from .models_conversation import ConversationSession
from .models_library import WORD_STATUSES, WordbookEntry, WordQuizQuestion, WordQuizV1

OPTION_IDS = ("A", "B", "C", "D", "E", "F")
BLANK = "○○○"
# 대화에서 만난 낱말이라는 사실만 남기는 정직한 대체 뜻풀이. 없는 뜻을 지어내지 않는다.
FALLBACK_MEANING = "대화에서 만난 낱말이에요. 무슨 뜻일지 내 말로 적어 볼까요?"


# --- 뜻풀이 ---------------------------------------------------------------------


def explain(
    db: Session, scope: ProfileScope, word: str, sentence: str, session: ConversationSession | None
) -> tuple[str, str, str]:
    """(뜻, 예문, 출처) — AI 를 못 쓰면 검수 사전, 그것도 없으면 직접 적어 보게 한다."""
    glossary = bank.GLOSSARY.get(word)
    if session is None or ai_gate.budget(scope.child, session) is not None:
        return (glossary or FALLBACK_MEANING, "" if glossary else sentence, "fallback")
    try:
        out = ai_gate.call(
            purpose="words.explain",
            instructions=prompt.words_instructions(),
            user_input=prompt.words_input(sentence),
            schema=WordExplainLLM,
        )
    except ai_gate.LlmError:
        return (glossary or FALLBACK_MEANING, "" if glossary else sentence, "fallback")
    picked = next((w for w in out.words if w.word == word), None) or next(
        (w for w in out.words if word in w.word or w.word in word), None
    )
    meaning = _clean_line(picked.meaning if picked else "", 160)
    if not meaning:
        return (glossary or FALLBACK_MEANING, "" if glossary else sentence, "fallback")
    return meaning, _clean_line(picked.example if picked else "", 200), "ai"


def _clean_line(text: str, limit: int) -> str:
    return safe_ai_line(text, limit) or ""


# --- 복습 간격 -------------------------------------------------------------------


def review_days(status: str) -> int:
    settings = get_settings()
    return {
        "NEW": settings.wordbook_review_days_new,
        "PRACTICING": settings.wordbook_review_days_practicing,
        "FAMILIAR": settings.wordbook_review_days_familiar,
    }[status]


def set_status(entry: WordbookEntry, status: str, now: datetime) -> None:
    entry.status = status
    entry.next_review_at = now + timedelta(days=review_days(status))
    entry.updated_at = now


def apply_review(entry: WordbookEntry, correct: bool, now: datetime) -> None:
    """맞히면 한 칸 오르고, 틀리면 한 칸 내려온다(NEW 아래는 없다). 점수는 남기지 않는다."""
    index = WORD_STATUSES.index(entry.status)
    moved = min(index + 1, len(WORD_STATUSES) - 1) if correct else max(index - 1, 0)
    entry.last_reviewed_at = now
    set_status(entry, WORD_STATUSES[moved], now)


# --- 직렬화 ---------------------------------------------------------------------


def entry_out(entry: WordbookEntry) -> WordbookEntryOut:
    return WordbookEntryOut(
        id=entry.id,
        word=entry.word,
        meaning=entry.meaning,
        example=entry.example,
        my_sentence=entry.my_sentence,
        status=entry.status,  # type: ignore[arg-type]
        source=entry.source,  # type: ignore[arg-type]
        source_conversation_id=entry.source_conversation_id,
        source_message_id=entry.source_message_id,
        source_sentence=entry.source_sentence,
        last_reviewed_at=iso(entry.last_reviewed_at),
        next_review_at=iso(entry.next_review_at),
        created_at=iso(entry.created_at) or "",
        updated_at=iso(entry.updated_at) or "",
    )


def summary(db: Session, profile_id: str) -> WordbookSummary:
    rows = list(db.scalars(select(WordbookEntry).where(WordbookEntry.profile_id == profile_id)))
    now = clock.now()
    counts = {status: sum(1 for r in rows if r.status == status) for status in WORD_STATUSES}
    return WordbookSummary(
        total=len(rows),
        new=counts["NEW"],
        practicing=counts["PRACTICING"],
        familiar=counts["FAMILIAR"],
        due_for_review=sum(1 for r in rows if r.next_review_at is None or r.next_review_at <= now),
    )


def quiz_out(db: Session, quiz: WordQuizV1) -> WordQuizOut:
    stmt = select(WordQuizQuestion).where(WordQuizQuestion.quiz_id == quiz.id).order_by(WordQuizQuestion.position)
    questions = list(db.scalars(stmt))
    return WordQuizOut(
        id=quiz.id,
        mode=quiz.mode,  # type: ignore[arg-type]
        status=quiz.status,  # type: ignore[arg-type]
        question_count=len(questions),
        answered_count=sum(1 for q in questions if q.answered_at is not None),
        questions=[
            QuizQuestionOut(
                id=q.id,
                index=q.position,
                prompt=q.prompt,
                options=[QuizOption(id=o["id"], label=o["label"]) for o in q.options],
                answered=q.answered_at is not None,
            )
            for q in questions
        ],
        created_at=iso(quiz.created_at) or "",
        completed_at=iso(quiz.completed_at),
    )


# --- 퀴즈 만들기 -----------------------------------------------------------------


def _blank_sentence(entry: WordbookEntry) -> str | None:
    for sentence in (entry.example, entry.source_sentence):
        if sentence and entry.word in sentence:
            return clip(sentence.replace(entry.word, BLANK), 200)
    return None


def _pick_entries(db: Session, profile_id: str, status: str | None, mode: str, count: int) -> list[WordbookEntry]:
    """복습할 때가 된 낱말을 먼저, 그다음 오래 담아 둔 순서로 고른다."""
    stmt = select(WordbookEntry).where(WordbookEntry.profile_id == profile_id)
    if status:
        stmt = stmt.where(WordbookEntry.status == status)
    rows = list(db.scalars(stmt))
    if mode == "FILL_IN_BLANK":
        rows = [r for r in rows if _blank_sentence(r)]
    if not rows:
        raise ApiError(409, "NO_WORDS_TO_QUIZ", "먼저 대화에서 낱말을 담아 볼까요?", {"mode": mode})
    now = clock.now()
    rows.sort(key=lambda r: (r.next_review_at is not None and r.next_review_at > now, r.next_review_at or r.created_at))
    return rows[:count]


def _answer_label(entry: WordbookEntry, mode: str) -> str:
    return entry.meaning if mode == "WORD_TO_MEANING" else entry.word


def _prompt(entry: WordbookEntry, mode: str) -> str:
    if mode == "WORD_TO_MEANING":
        return f"‘{entry.word}’의 뜻은 무엇일까?"
    if mode == "MEANING_TO_WORD":
        return f"‘{entry.meaning}’ 은 어떤 낱말일까?"
    return f"빈칸에 들어갈 낱말은? {_blank_sentence(entry)}"


def _pool(db: Session, profile_id: str, mode: str) -> list[str]:
    rows = list(db.scalars(select(WordbookEntry).where(WordbookEntry.profile_id == profile_id)))
    if mode == "WORD_TO_MEANING":
        return list(dict.fromkeys([r.meaning for r in rows] + list(bank.GLOSSARY.values())))
    return list(dict.fromkeys([r.word for r in rows] + list(bank.GLOSSARY.keys())))


def create_quiz(db: Session, scope: ProfileScope, mode: str, count: int | None, status: str | None) -> WordQuizV1:
    settings = get_settings()
    size = min(count or settings.word_quiz_default_count, settings.word_quiz_max_count)
    entries = _pick_entries(db, scope.profile_id, status, mode, size)
    quiz = WordQuizV1(
        profile_id=scope.profile_id, user_id=scope.user.id, mode=mode, created_at=clock.now(), status="IN_PROGRESS"
    )
    db.add(quiz)
    db.flush()
    rng = random.Random(quiz.id)
    pool = _pool(db, scope.profile_id, mode)
    option_count = min(settings.word_quiz_option_count, len(OPTION_IDS))
    for position, entry in enumerate(entries):
        answer = _answer_label(entry, mode)
        distractors = [item for item in pool if item != answer]
        rng.shuffle(distractors)
        labels = [answer, *distractors[: option_count - 1]]
        rng.shuffle(labels)
        options = [{"id": OPTION_IDS[i], "label": clip(label, 160)} for i, label in enumerate(labels)]
        db.add(
            WordQuizQuestion(
                quiz_id=quiz.id,
                entry_id=entry.id,
                position=position,
                prompt=clip(_prompt(entry, mode), 200),
                options=options,
                answer_option_id=next(o["id"] for o in options if o["label"] == clip(answer, 160)),
            )
        )
    db.flush()
    return quiz


# --- 채점 -----------------------------------------------------------------------


def answer_question(
    db: Session, quiz: WordQuizV1, question_id: str, option_id: str
) -> tuple[WordQuizQuestion, WordbookEntry, bool]:
    question = db.get(WordQuizQuestion, question_id)
    if question is None or question.quiz_id != quiz.id:
        raise ApiError(404, "QUIZ_QUESTION_NOT_FOUND", "퀴즈 문제를 찾을 수 없어요.")
    if question.answered_at is not None:
        raise ApiError(409, "ALREADY_ANSWERED", "이미 답한 문제예요.", {"questionId": question.id})
    if option_id not in {o["id"] for o in question.options}:
        raise ApiError(400, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": ["optionId"]})
    now = clock.now()
    correct = option_id == question.answer_option_id
    question.chosen_option_id = option_id
    question.correct = correct
    question.answered_at = now
    entry = db.get(WordbookEntry, question.entry_id)
    if entry is not None:
        apply_review(entry, correct, now)
    remaining = db.scalars(
        select(WordQuizQuestion).where(WordQuizQuestion.quiz_id == quiz.id, WordQuizQuestion.answered_at.is_(None))
    ).all()
    if not remaining:
        quiz.status = "COMPLETED"
        quiz.completed_at = now
    return question, entry, correct  # type: ignore[return-value]
