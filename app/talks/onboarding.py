"""첫 만남 대화 — 별명, 소속+학년, 좋아하는 것, 키우고 싶은 것을 대화로 받아 정리한다.

학교 이름·주소·실명은 저장하지 않는다. 소속은 종류(초등학교/홈스쿨/기타)만, 학년은 숫자만 남긴다.
AI 를 쓸 수 없을 때는 지금 묻고 있는 항목 하나를 규칙으로 뽑는다.
"""

from __future__ import annotations

import re

from ..safety import topics as sensitive

FIELDS: tuple[str, ...] = ("nickname", "school", "likes", "want_to_learn")
AFFILIATIONS: tuple[str, ...] = ("elementary", "homeschool", "other")

QUESTIONS: dict[str, str] = {
    "intro": (
        "안녕! 나는 너랑 같이 생각하고 이야기하는 AI 생각 친구야. "
        "우리 친해지려면 먼저 너를 뭐라고 부르면 좋을지 알려 줄래? 진짜 이름 말고 별명도 좋아."
    ),
    "nickname": "너를 뭐라고 부르면 좋을까? 별명을 알려 줘.",
    "school": "{nickname}, 반가워! 너는 초등학교 몇 학년이야? 학교 이름은 말하지 않아도 돼.",
    "likes": "너는 뭘 좋아해? 좋아하는 놀이, 동물, 음식 뭐든 괜찮아.",
    "want_to_learn": "요즘 더 알고 싶거나 키우고 싶은 게 있어? 과학, 수학, 역사, 아니면 다른 것도 좋아.",
    "done": "고마워! 네가 알려 준 걸로 우리만의 이야기를 시작할게. 내가 정리한 게 맞는지 확인해 줄래?",
}

_NUM = {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "일": 1, "한": 1, "이": 2, "두": 2, "삼": 3, "세": 3}
_NUM.update({"사": 4, "네": 4, "오": 5, "다섯": 5, "육": 6, "여섯": 6})
_GRADE = re.compile(r"(다섯|여섯|[1-6]|일|한|이|두|삼|세|사|네|오|육)\s*학년")
_NICK_PREFIX = re.compile(r"^(내\s*)?(별명은|이름은|나는|저는|난)\s*")
_NICK_SUFFIX = re.compile(r"(이라고|라고)?\s*(불러\s*줘|불러|해|이야|야|예요|이에요|입니다|요)?[.!~\s]*$")
_LIST_SPLIT = re.compile(r"\s*,\s*|(?<=[가-힣])(?:하고|이랑|랑|와|과)\s+|\s+그리고\s+|\s+")
_LIKE_TAIL = re.compile(
    r"(을|를)?\s*(더|정말|제일|진짜)?\s*"
    r"(좋아해요|좋아해|좋아요|좋아|알고\s*싶어|배우고\s*싶어|궁금해|잘하고\s*싶어|키우고\s*싶어)[.!~\s]*$"
)
_STOP = {"나", "나는", "저", "저는", "난", "내가", "우리", "그리고", "좋아", "정말", "진짜", "제일", "더", "것", "거"}


def missing_fields(profile: dict) -> list[str]:
    missing = []
    if not profile.get("nickname"):
        missing.append("nickname")
    if not profile.get("grade"):
        missing.append("school")
    if not profile.get("likes"):
        missing.append("likes")
    if not profile.get("want_to_learn"):
        missing.append("want_to_learn")
    return missing


def question_for(field: str | None, profile: dict) -> str:
    if field is None:
        return QUESTIONS["done"]
    return QUESTIONS[field].format(nickname=profile.get("nickname") or "친구")


def parse_grade(text: str) -> int | None:
    hit = _GRADE.search(text or "")
    return _NUM.get(hit.group(1)) if hit else None


def parse_affiliation(text: str) -> str | None:
    t = text or ""
    if re.search(r"초등|초교", t):
        return "elementary"
    if re.search(r"홈스쿨|집에서\s*공부", t):
        return "homeschool"
    if re.search(r"유치원|중학|대안학교", t):
        return "other"
    return None


def clean_nickname(value: str | None) -> str | None:
    if not value:
        return None
    v = re.sub(r"[^가-힣A-Za-z0-9 ]", "", value).strip()
    if not v or len(v) > 10 or "●" in value or sensitive.detect(v):
        return None
    return v


def parse_nickname(text: str) -> str | None:
    t = _NICK_SUFFIX.sub("", _NICK_PREFIX.sub("", (text or "").strip()))
    tokens = t.split()
    return clean_nickname(tokens[0] if tokens else None)


def clean_items(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        v = re.sub(r"(을|를|이|가|은|는|도)$", "", re.sub(r"[^가-힣A-Za-z0-9 ]", "", item).strip())
        if v and v not in _STOP and len(v) <= 15 and v not in out and not sensitive.detect(v):
            out.append(v)
    return out[:5]


def parse_list(text: str) -> list[str]:
    t = _LIKE_TAIL.sub("", (text or "").strip())
    return clean_items([p for p in _LIST_SPLIT.split(t) if p])


def apply_rules(profile: dict, field: str, original: str, masked: str) -> dict:
    """지금 묻는 항목 하나를 규칙으로 채운다. 학년·소속 종류는 원문에서 읽되 학교 이름은 버린다."""
    updated = dict(profile)
    grade, affiliation = parse_grade(original), parse_affiliation(original)
    if grade:
        updated["grade"] = grade
    if affiliation:
        updated["affiliation"] = affiliation
    if field == "nickname":
        updated["nickname"] = parse_nickname(masked) or updated.get("nickname")
    elif field == "likes":
        updated["likes"] = parse_list(masked) or updated.get("likes", [])
    elif field == "want_to_learn":
        updated["want_to_learn"] = parse_list(masked) or updated.get("want_to_learn", [])
    return updated
