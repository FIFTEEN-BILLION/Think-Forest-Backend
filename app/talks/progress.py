"""성장 기록 — 점수 없이 빈도와 성취 기준만 센다.

성취 기준은 대화에서 실제로 한 행동(문장으로 한 답과 그 답이 어떤 질문에 대한 것이었는지)으로만 센다.
단계(씨앗·새싹·나무)는 횟수 구간일 뿐 평가가 아니다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Book, Child, Story, Talk, Turn, Word
from .topics import WEEKDAY_LABELS

ACHIEVEMENTS: tuple[tuple[str, str, str], ...] = (
    ("reason_sentence", "이유를 문장으로 말하기", "'왜냐하면', '때문에'처럼 이유를 넣어 말했어요."),
    ("new_idea", "꼬리질문에 새 생각 보태기", "더 깊은 질문에 새로운 생각을 보탰어요."),
    ("imagination", "상상해서 이야기하기", "상상 장면 속 이야기를 문장으로 들려줬어요."),
    ("think_again", "생각을 다시 살펴보기", "생각을 지키거나 바꾸면서 이유를 말했어요."),
    ("compose", "긴 문장으로 정리하기", "이야기를 세 문장 이상으로 정리했어요."),
    ("finish", "15분 이야기 끝까지 하기", "한 이야기를 끝까지 나눴어요."),
    ("word_use", "새 단어 써 보기", "보관한 단어를 내 문장에 썼어요."),
)
STAGES: tuple[tuple[int, str], ...] = ((10, "나무"), (3, "새싹"), (1, "씨앗"))
TARGETS: tuple[int, ...] = (1, 3, 10)
LEARNED_CORRECT = 2  # 퀴즈에서 두 번 맞히면 '익힌 단어'


def stage(count: int) -> str | None:
    return next((name for threshold, name in STAGES if count >= threshold), None)


def compute(db: Session, child: Child, now: datetime | None = None) -> dict:
    now = now or clock.now()
    talks = list(db.scalars(select(Talk).where(Talk.child_id == child.id)))
    turns = (
        list(
            db.scalars(
                select(Turn)
                .where(Turn.talk_id.in_([t.id for t in talks]), Turn.role == "child")
                .order_by(Turn.created_at)
            )
        )
        if talks
        else []
    )
    words = list(db.scalars(select(Word).where(Word.child_id == child.id)))

    counts = dict.fromkeys((a[0] for a in ACHIEVEMENTS), 0)
    for turn in turns:
        if not turn.sentence_ok:
            continue
        meta = turn.meta or {}
        if meta.get("reason_given"):
            counts["reason_sentence"] += 1
        if turn.move in ("tail", "challenge", "connect") and meta.get("new_idea"):
            counts["new_idea"] += 1
        if turn.move == "imagine":
            counts["imagination"] += 1
        if turn.move == "reason_check" and meta.get("stance") in ("kept", "changed"):
            counts["think_again"] += 1
        if turn.move == "compose" and meta.get("sentences", 0) >= 3:
            counts["compose"] += 1
        if any(w.word in turn.text and w.created_at <= turn.created_at for w in words):
            counts["word_use"] += 1
    counts["finish"] = sum(1 for t in talks if t.status == "completed")

    days = {clock.kst(t.created_at).date() for t in turns}
    today = clock.kst(now).date()
    last7 = [today - timedelta(days=i) for i in range(6, -1, -1)]

    return {
        "frequency": {
            "talks_started": len(talks),
            "talks_completed": counts["finish"],
            "active_minutes": sum(t.active_seconds for t in talks) // 60,
            "days_active_7": sum(1 for d in last7 if d in days),
            "days_active_30": sum(1 for d in days if (today - d).days < 30),
            "last7": [
                {"date": d.isoformat(), "weekday_label": WEEKDAY_LABELS[d.weekday()], "active": d in days}
                for d in last7
            ],
        },
        "achievements": [
            {
                "id": key,
                "title": title,
                "description": description,
                "count": counts[key],
                "stage": stage(counts[key]),
                "next_target": next((t for t in TARGETS if t > counts[key]), None),
            }
            for key, title, description in ACHIEVEMENTS
        ],
        "words": {"saved": len(words), "learned": sum(1 for w in words if w.quiz_correct >= LEARNED_CORRECT)},
        "stories": db.scalar(select(func.count()).select_from(Story).where(Story.child_id == child.id)) or 0,
        "books": db.scalar(select(func.count()).select_from(Book).where(Book.child_id == child.id)) or 0,
    }
