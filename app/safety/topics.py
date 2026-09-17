"""민감 주제 1차 필터 — 금칙어와 함께 LLM 호출 전·후에 돌린다.

막는 것: 선정적·정치적·폭력적 주제, 외모·몸 이야기, 개인정보 요청, 자해 신호.
자해 신호는 대화를 돌리는 데서 끝내지 않고 보호자에게 알릴 사건으로 남긴다(escalate).
아동 안전 필터라 과차단 쪽으로 기운다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import blocklist

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("self_harm", re.compile(r"자살|자해|죽고\s*싶|사라지고\s*싶|살기\s*싫")),
    ("sexual", re.compile(r"야한|성관계|음란|알몸|벗은\s*몸|섹스")),
    ("violence", re.compile(r"죽이|죽여|때려\s*죽|피가\s*철철|칼로\s*찌|총으로\s*쏘")),
    ("politics", re.compile(r"대통령|정당|선거|국회의원|여당|야당|탄핵|좌파|우파|정치인")),
    (
        "appearance",
        re.compile(
            r"어떻게\s*생겼|생김새|외모|못\s*생|잘\s*생|예쁘게\s*생|얼굴이\s*(어때|어떻|예뻐)|몸무게|뚱뚱|날씬"
        ),
    ),
    (
        "personal_info",
        re.compile(
            r"(학교|집)\s*(이름|주소)|주소\s*(가|를|좀|알려)|전화\s*번호"
            r"|사진\s*(보내|찍어|올려)|비밀\s*번호"
        ),
    ),
)

REDIRECTS: dict[str, str] = {
    "self_harm": "그런 마음이 들었다면 정말 힘들었겠다. 지금 가족이나 선생님처럼 믿을 수 있는 어른에게 꼭 이야기해 줘.",
    "sexual": (
        "그 이야기는 여기서 나누지 않을게. 궁금한 게 있으면 믿을 수 있는 어른에게 물어봐 줘. 우리 주제로 돌아가 볼까?"
    ),
    "violence": "무섭거나 아픈 이야기는 믿을 수 있는 어른에게 꼭 알려 줘. 우리는 오늘 주제로 돌아가 볼까?",
    "politics": "그 이야기는 가족과 함께 나눠 보면 좋겠어. 우리는 오늘 주제로 돌아가 볼까?",
    "appearance": "생김새보다 네 생각이 더 궁금해! 오늘 주제에서 떠오른 생각을 들려줄래?",
    "personal_info": "이름, 학교, 주소, 사진 같은 건 말하지 않아도 돼. 오늘 주제로 돌아가 볼까?",
    "blocked_term": "그 말은 여기서 쓰지 않을게. 오늘 주제로 돌아가서 네 생각을 들려줄래?",
    "moderation": "그 이야기는 여기서 나누기 어려워. 오늘 주제로 돌아가 볼까?",
}


@dataclass(frozen=True)
class TopicFlag:
    category: str

    @property
    def escalate(self) -> bool:
        return self.category == "self_harm"

    @property
    def redirect(self) -> str:
        return REDIRECTS[self.category]


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def detect(text: str) -> TopicFlag | None:
    """민감 주제면 TopicFlag, 아니면 None. 자해 신호를 가장 먼저 본다."""
    normalized = _normalize(text)
    compact = re.sub(r"\s+", "", normalized)
    for category, pattern in _PATTERNS:
        if pattern.search(normalized) or pattern.search(compact):
            return TopicFlag(category)
    if blocklist.find_blocked(text):
        return TopicFlag("blocked_term")
    return None
