"""주제 은행과 요일 테마.

주제를 먼저 던져 대화를 연다. 사실(facts)은 검수 대상이며 AI 는 이 목록 밖의 사실을 보태지 않는다.
아이가 '!' 버튼으로 만든 카테고리와 보호자·친구가 공유한 모험도 같은 스냅숏 형태로 대화에 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .. import clock

VISUAL_KEYS: tuple[str, ...] = (
    "thinking", "question", "lightbulb", "magnifier", "puzzle", "castle", "book", "star", "calendar",
    "snow", "sled", "ice", "water_drop", "airplane", "cloud", "car", "pizza", "stairs", "fire", "hangul",
    "school_bag", "sun", "moon", "tree", "animal", "rocket", "heart", "rainbow", "music", "family",
)  # fmt: skip

CATEGORIES: dict[str, dict[str, str]] = {
    "science": {"name": "과학", "visual": "magnifier"},
    "math": {"name": "수학", "visual": "puzzle"},
    "history": {"name": "역사", "visual": "castle"},
    "thinking": {"name": "생각놀이", "visual": "lightbulb"},
    "diary": {"name": "오늘 일기", "visual": "book"},
    "custom": {"name": "내가 고른 주제", "visual": "star"},
}

# 요일마다 다른 느낌. 0=월요일(KST). color·mood 는 프론트가 화면 분위기를 바꾸는 키다.
WEEKDAY_THEMES: tuple[dict[str, str], ...] = (
    {"category": "science", "title": "과학 탐험의 날", "color": "teal", "mood": "curious"},
    {"category": "math", "title": "수학 퍼즐의 날", "color": "gold", "mood": "puzzle"},
    {"category": "history", "title": "옛날 이야기의 날", "color": "coral", "mood": "story"},
    {"category": "thinking", "title": "생각 놀이의 날", "color": "purple", "mood": "playful"},
    {"category": "science", "title": "상상 실험의 날", "color": "sky", "mood": "imagine"},
    {"category": "diary", "title": "오늘 일기의 날", "color": "green", "mood": "cozy"},
    {"category": "custom", "title": "내가 고른 주제의 날", "color": "pink", "mood": "free"},
)
WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")


@dataclass(frozen=True)
class Topic:
    id: str
    category: str
    title: str
    hook: str
    visual: str
    imagine: str
    challenge: str
    connect: str
    facts: tuple[str, ...] = ()
    glossary: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    review_status: str = "needs_review"


TOPICS: tuple[Topic, ...] = (
    Topic(
        "ice_cup", "science", "얼음물 컵의 물방울",
        "얼음물을 컵에 담아 두면 컵 바깥에 물방울이 생겨. 그 물은 어디에서 왔을까?", "water_drop",
        imagine="네가 개미만큼 작아져서 차가운 컵 옆에 서 있다고 상상해 보자. 주변 공기에서 무슨 일이 일어날까?",
        challenge="그 물이 컵 안에서 새어 나온 거라면, 빈 컵을 차갑게 만들어도 물방울이 생길까?",
        connect="차가운 물건에 물방울이 맺히는 걸 생활에서 또 어디서 봤어?",
        facts=(
            "공기 속에는 눈에 보이지 않는 수증기가 있어요.",
            "차가운 컵 겉면에 닿은 수증기가 식으면 물방울로 바뀌어요.",
        ),
        glossary=(("수증기", "물이 눈에 안 보이는 기체가 된 것"), ("겉면", "물건의 바깥쪽 면")),
    ),
    Topic(
        "airplane", "science", "하늘을 나는 비행기",
        "하늘에 날아다니는 비행기를 본 적 있니? 그렇게 무거운 비행기는 어떻게 하늘에 떠 있을까?", "airplane",
        imagine="네가 비행기 조종사가 되어 활주로를 힘껏 달리고 있다고 상상해 보자. 날개 주변에서는 무슨 일이 생길까?",
        challenge="날개가 없는 비행기도 하늘을 날 수 있을까?",
        connect="비행기처럼 공기의 도움을 받아 나는 것을 또 본 적 있어?",
        facts=("비행기가 빠르게 달리면 날개 주변의 공기가 비행기를 위로 밀어 올리는 힘이 생겨요.",),
        glossary=(("활주로", "비행기가 달리다가 뜨고 내리는 긴 길"), ("조종사", "비행기를 움직이는 사람")),
    ),
    Topic(
        "snow", "science", "하얀 눈",
        "눈이 오는 날을 본 적 있니? 눈으로 어떤 걸 할 수 있을까?", "snow",
        imagine="자, 네가 썰매장에 갔다고 상상하고 자유롭게 이야기해 보자. 무엇이 보이고 어떤 소리가 들려?",
        challenge="그런데 썰매장에 쌓인 눈은 모두 하늘에서 내려 쌓인 진짜 눈일까?",
        connect="눈을 어디에 쓸 수 있을지 새로운 방법을 떠올려 볼래?",
        facts=(
            "눈은 하늘의 아주 작은 얼음 알갱이가 모여 내려오는 거예요.",
            "썰매장이나 스키장은 기계로 인공 눈을 만들기도 해요.",
        ),
        glossary=(("인공", "사람이 만든 것"), ("알갱이", "아주 작은 덩어리")),
    ),
    Topic(
        "pizza_share", "math", "피자 나누기",
        "피자 한 판을 친구 네 명이 똑같이 나눠 먹으려면 어떻게 자르면 좋을까?", "pizza",
        imagine="피자를 다 잘랐는데 친구가 한 명 더 왔다고 상상해 보자. 어떻게 하면 좋을까?",
        challenge="조각 수가 같으면 모두 똑같이 먹은 걸까?",
        connect="피자 말고 무언가를 똑같이 나눠야 했던 적이 있어?",
        facts=("똑같이 나누려면 조각의 개수뿐 아니라 크기도 같아야 해요.",),
        glossary=(("똑같이", "크기나 양이 서로 같게"),),
    ),
    Topic(
        "stairs_pattern", "math", "계단의 규칙",
        "계단을 한 칸씩 오를 때와 두 칸씩 오를 때, 무엇이 달라질까?", "stairs",
        imagine="아주 긴 계단 앞에 서 있다고 상상해 보자. 어떤 방법으로 올라가면 가장 좋을까?",
        challenge="두 칸씩 오르면 언제나 딱 절반만 걸으면 될까?",
        connect="생활에서 규칙이 반복되는 걸 본 적 있어?",
        facts=("두 칸씩 오르면 걸음 수가 거의 절반으로 줄어요. 칸 수가 홀수면 한 걸음이 더 필요해요.",),
        glossary=(("규칙", "계속 반복되는 약속이나 모양"), ("홀수", "둘씩 짝을 지으면 하나가 남는 수")),
    ),
    Topic(
        "old_fire", "history", "옛날 사람들의 불",
        "아주 옛날, 성냥이나 라이터가 없던 때에는 사람들이 어떻게 불을 피웠을까?", "fire",
        imagine="네가 아주 먼 옛날 마을에 살고 있다고 상상해 보자. 추운 밤에 무엇을 할까?",
        challenge="불을 피우는 방법을 알아낸 사람은 처음부터 방법을 알고 있었을까?",
        connect="요즘 우리는 불 대신 무엇으로 따뜻하게 지내?",
        facts=("옛날 사람들은 나무를 비비거나 돌을 부딪쳐 불씨를 만들었어요.",),
        glossary=(("불씨", "불을 붙일 수 있는 작은 불"),),
    ),
    Topic(
        "hangul", "history", "한글 이야기",
        "우리가 쓰는 한글이 없던 옛날에는 사람들이 어떻게 글을 썼을까?", "hangul",
        imagine="글자를 모르는 마을 사람이 멀리 사는 가족에게 소식을 전하고 싶어 한다고 상상해 보자. 어떤 기분일까?",
        challenge="글자가 어려우면 사람들에게 어떤 불편한 일이 생겼을까?",
        connect="한글이 있어서 네가 할 수 있는 일은 뭐가 있어?",
        facts=("한글은 조선의 세종대왕이 백성이 쉽게 쓸 수 있도록 만들었어요.",),
        glossary=(("백성", "옛날 나라에 사는 보통 사람들"),),
    ),
    Topic(
        "what_is_car", "thinking", "자동차는 어떤 물건일까",
        "자동차는 어떤 물건인 것 같아? 네 말로 설명해 볼래?", "car",
        imagine="자동차가 말을 할 수 있다고 상상해 보자. 자동차는 우리에게 무슨 말을 할까?",
        challenge="바퀴가 없어도 자동차라고 할 수 있을까?",
        connect="자동차가 없으면 우리 생활은 어떻게 달라질까?",
    ),
    Topic(
        "no_rules_class", "thinking", "규칙이 없는 교실",
        "만약 우리 반에 규칙이 하나도 없다면 어떤 일이 생길까?", "school_bag",
        imagine="규칙이 없는 교실에서 하루를 보낸다고 상상해 보자. 아침부터 무슨 일이 생길까?",
        challenge="규칙이 많을수록 언제나 더 좋은 걸까?",
        connect="집에서 우리 가족이 지키는 규칙은 뭐가 있어?",
        glossary=(("규칙", "함께 지키기로 한 약속"),),
    ),
    Topic(
        "today", "diary", "오늘 있었던 일",
        "오늘 있었던 일 중에 가장 기억에 남는 일을 이야기해 줄래?", "calendar",
        imagine="그 일이 다시 일어난다면 이번에는 어떻게 해 보고 싶어?",
        challenge="그때 함께 있던 친구나 가족은 어떤 마음이었을까?",
        connect="그 일로 새로 알게 되거나 느낀 게 있어?",
    ),
)  # fmt: skip

GLOSSARY: dict[str, str] = {word: meaning for t in TOPICS for word, meaning in t.glossary}


def get_topic(topic_id: str) -> Topic | None:
    return next((t for t in TOPICS if t.id == topic_id), None)


def today_theme(at: datetime | None = None) -> dict[str, str | int]:
    weekday = clock.kst(at or clock.now()).weekday()
    theme = WEEKDAY_THEMES[weekday]
    return {
        **theme,
        "weekday": weekday,
        "weekdayLabel": WEEKDAY_LABELS[weekday],
        "visual": CATEGORIES[theme["category"]]["visual"],
    }


def pick_topic(category: str, recent_topic_ids: list[str]) -> Topic | None:
    """카테고리 안에서 최근에 덜 쓴 주제를 고른다. 은행에 없으면 None."""
    candidates = [t for t in TOPICS if t.category == category]
    if not candidates:
        return None
    fresh = [t for t in candidates if t.id not in recent_topic_ids]
    if fresh:
        return fresh[0]
    return min(candidates, key=lambda t: recent_topic_ids.index(t.id) if t.id in recent_topic_ids else -1)


def snapshot(topic: Topic) -> dict:
    return {
        "id": topic.id,
        "category": topic.category,
        "title": topic.title,
        "hook": topic.hook,
        "visual": topic.visual,
        "imagine": topic.imagine,
        "challenge": topic.challenge,
        "connect": topic.connect,
        "facts": list(topic.facts),
        "glossary": [{"word": w, "meaning": m} for w, m in topic.glossary],
        "reviewStatus": topic.review_status,
        "source": "bank",
    }


def custom_snapshot(category_name: str, title: str, hook: str, visual: str = "star") -> dict:
    return {
        "id": None,
        "category": "custom",
        "title": title,
        "hook": hook,
        "visual": visual if visual in VISUAL_KEYS else "star",
        "imagine": f"'{category_name}' 속으로 들어갔다고 상상해 보자. 무엇이 보이고 무슨 일이 생길까?",
        "challenge": "정말 언제나 그럴까? 아닐 수도 있는 경우를 떠올려 볼래?",
        "connect": f"'{category_name}'에 대해 생각한 것을 우리 생활 어디에서 볼 수 있을까?",
        "facts": [],
        "glossary": [],
        "reviewStatus": "unreviewed",
        "source": "custom",
    }


def adventure_snapshot(body: dict, title: str) -> dict:
    follow = list(body.get("followUps") or [])
    snap = custom_snapshot(title, title, body.get("hook", ""), body.get("visual", "star"))
    snap["category"] = body.get("category", "custom")
    for key, value in zip(("connect", "challenge", "imagine"), follow, strict=False):
        snap[key] = value
    snap["source"] = "shared"
    return snap
