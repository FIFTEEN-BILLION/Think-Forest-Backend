"""v1 주제 카탈로그 — 기존 주제 은행(`talks/topics.py`)과 아이가 만든 주제를 v1 id·카테고리로 보여 준다.

- 은행 주제 id 는 `topic_<기존 id>`, 사용자 주제 id 는 `topic_user_…`.
- 카테고리는 대문자 enum(SCIENCE, MATH, HISTORY, THINKING, DAILY_LIFE, NATURE, FEELINGS, IMAGINATION).
- 대화에는 기존과 같은 주제 스냅숏(검수 사실·참고 질문 포함)을 저장하고, v1 표시용 키만 덧붙인다.
"""

from __future__ import annotations

import math
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from ..talks import topics as bank
from .korean import josa
from .models_conversation import ChildProfile, ConversationSession, UserTopic

BANK_PREFIX = "topic_"
USER_PREFIX = "topic_user_"
CATEGORY_OF_BANK = {
    "science": "SCIENCE",
    "math": "MATH",
    "history": "HISTORY",
    "thinking": "THINKING",
    "diary": "DAILY_LIFE",
    "custom": "IMAGINATION",
}
CATEGORY_NAMES = {
    "SCIENCE": "과학",
    "MATH": "수학",
    "HISTORY": "역사",
    "THINKING": "생각놀이",
    "DAILY_LIFE": "생활",
    "NATURE": "자연",
    "FEELINGS": "마음",
    "IMAGINATION": "상상",
}

# 이야기를 여는 경험 질문(고르기만 해도 답할 수 있게 SINGLE_CHOICE 로 묻는다).
OPENERS: dict[str, str] = {
    "ice_cup": "얼음물 컵 밖에 물방울이 생긴 걸 본 적 있어?",
    "airplane": "하늘을 나는 비행기를 본 적 있어?",
    "snow": "눈 오는 날 밖에서 놀아 본 적 있어?",
    "pizza_share": "친구랑 간식을 똑같이 나눠 먹어 본 적 있어?",
    "stairs_pattern": "계단을 두 칸씩 올라가 본 적 있어?",
    "old_fire": "캠핑장이나 그림책에서 모닥불을 본 적 있어?",
    "hangul": "한글로 편지나 쪽지를 써 본 적 있어?",
    "what_is_car": "자동차를 타고 멀리 가 본 적 있어?",
    "no_rules_class": "교실에서 친구들이랑 규칙을 정해 본 적 있어?",
    "today": "오늘 기억에 남는 일이 있었어?",
}
DEFAULT_OPENER = "이 이야기랑 비슷한 걸 본 적이나 해 본 적 있어?"
OPENER_OPTIONS = [{"id": "SEEN", "label": "응, 있어"}, {"id": "NOT_SEEN", "label": "아니, 없어"}]


def estimated_minutes() -> int:
    return max(10, math.ceil(get_settings().conversation_min_seconds / 60) + 5)


def bank_id(topic: bank.Topic) -> str:
    return f"{BANK_PREFIX}{topic.id}"


def _bank_snapshot(topic: bank.Topic) -> dict:
    return {
        **bank.snapshot(topic),
        "apiId": bank_id(topic),
        "apiCategory": CATEGORY_OF_BANK[topic.category],
        "opener": OPENERS.get(topic.id, DEFAULT_OPENER),
    }


def _user_hook(title: str) -> str:
    return f"{title} 네 생각은 어때?" if title.endswith("?") else f"‘{title}’ 하면 어떤 생각이 떠올라?"


def _user_snapshot(topic: UserTopic) -> dict:
    snap = bank.custom_snapshot(CATEGORY_NAMES.get(topic.category, "상상"), topic.title, _user_hook(topic.title))
    snap.update(
        {
            "imagine": "그 장면 속에 네가 들어갔다고 상상해 보자. 무엇이 보여?",
            "connect": "이 생각을 우리 생활 어디에서 볼 수 있을까?",
            "apiId": topic.id,
            "apiCategory": topic.category,
            "opener": DEFAULT_OPENER,
            "source": "user",
        }
    )
    return snap


def resolve(db: Session, user_id: str, topic_id: str) -> dict | None:
    """대화에 쓸 주제 스냅숏. 없거나 다른 사람 주제면 None."""
    if topic_id.startswith(USER_PREFIX):
        topic = db.get(UserTopic, topic_id)
        return _user_snapshot(topic) if topic and topic.user_id == user_id else None
    if topic_id.startswith(BANK_PREFIX):
        found = bank.get_topic(topic_id.removeprefix(BANK_PREFIX))
        return _bank_snapshot(found) if found else None
    return None


def all_topics(db: Session, user_id: str) -> list[dict]:
    """은행 주제(기존 순서) 뒤에 내가 만든 주제(최신순)."""
    mine = db.scalars(select(UserTopic).where(UserTopic.user_id == user_id).order_by(UserTopic.created_at.desc()))
    return [*(_bank_snapshot(t) for t in bank.TOPICS), *(_user_snapshot(t) for t in mine)]


def questions(snapshot: dict) -> list[str]:
    return [
        q
        for q in (snapshot.get("hook"), snapshot.get("connect"), snapshot.get("challenge"), snapshot.get("imagine"))
        if q
    ]


# --- 추천 ---------------------------------------------------------------------


def recommend(db: Session, user_id: str, profile: ChildProfile | None, limit: int = 3) -> list[tuple[dict, str]]:
    """요일 테마와 관심사로 고른 주제와 사람이 읽을 추천 이유. 최근 대화한 주제는 뒤로 미룬다."""
    theme = bank.today_theme()
    since = clock.now() - timedelta(days=14)
    recent = [
        (s.topic or {}).get("id")
        for s in db.scalars(
            select(ConversationSession)
            .where(
                ConversationSession.user_id == user_id,
                ConversationSession.kind == "STORY",
                ConversationSession.created_at >= since,
            )
            .order_by(ConversationSession.created_at.desc())
            .limit(20)
        )
    ]
    interests = list(profile.interests) if profile else []
    picks: list[tuple[dict, str]] = []

    def add(topic: bank.Topic | None, reason: str) -> None:
        if topic and len(picks) < limit and all(p[0]["id"] != topic.id for p in picks):
            picks.append((_bank_snapshot(topic), reason))

    for topic in bank.TOPICS:
        hit = next((i for i in interests if i in topic.title or i in topic.hook), None)
        if hit and topic.id not in recent:
            add(topic, f"{josa(hit, '을', '를')} 좋아한다고 해서 골랐어요.")
    order = [
        theme["category"],
        *(c for c in ("science", "math", "history", "thinking", "diary") if c != theme["category"]),
    ]
    for category in order:
        topic = bank.pick_topic(category, recent)
        if category == theme["category"]:
            add(topic, f"오늘은 {theme['title']}이에요.")
        elif topic and topic.id not in recent:
            add(topic, f"아직 이야기해 보지 않은 {CATEGORY_NAMES[CATEGORY_OF_BANK[category]]} 주제예요.")
        else:
            add(topic, f"다시 생각해 보면 새로운 게 보이는 {CATEGORY_NAMES[CATEGORY_OF_BANK[category]]} 주제예요.")
    return picks
