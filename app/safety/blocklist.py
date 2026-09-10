"""1차 금칙어 필터 — 문자열 포함 검사.

반드시 Claude 호출 전에 돌린다. 여기서 막히면 토큰 비용 0.
2차(생성 단계 AI 심사)와 순서를 바꾸지 말 것. 비용 설계이자 안전 설계다.

우회 대응: 매칭 전에 공백·구두점·반복문자를 제거해 "칼 로", "칼.로", "칼로로"
같은 변형도 걸리게 한다. 아동 안전 필터라 과차단(false positive) 쪽으로 기운다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 아동 콘텐츠 생성 요청에서 즉시 걸러야 하는 표현.
# 카테고리: 폭력/무기, 성적, 자해, 혐오, 범죄, 약물.
BLOCKED_TERMS: tuple[str, ...] = (
    # 폭력·무기
    "죽여", "죽이", "죽인다", "살인", "칼로", "칼부림", "총으로", "총기",
    "폭행", "때려죽", "패죽", "목졸라", "목을졸", "피가철철", "시체",
    # 성적
    "성관계", "야한", "음란", "성적으로", "야동", "19금", "섹스",
    # 자해·자살
    "자살", "자해", "목매", "손목긋", "죽고싶",
    # 혐오·차별
    "혐오", "장애인비하", "인종차별", "성차별비하", "김치녀", "한남충",
    # 범죄·약물
    "마약", "대마초", "필로폰", "폭탄만들", "사제폭탄", "해킹하는법",
    "몰래카메라", "몰카", "도박사이트",
)

# 정규화 시 제거할 문자: 공백류 + 흔한 구분 기호.
_STRIP = re.compile(r"[\s.\-_·・*~^,'\"`|/\\()\[\]{}]+")
# 같은 문자 반복을 1번으로 축약(늘려쓰기 우회 방지). 안전 필터라 과도해도 무방.
_RUN = re.compile(r"(.)\1+")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = _STRIP.sub("", text)
    text = _RUN.sub(r"\1", text)
    return text


_NORMALIZED_TERMS: tuple[tuple[str, str], ...] = tuple(
    (term, _normalize(term)) for term in BLOCKED_TERMS
)


@dataclass(frozen=True)
class BlockResult:
    blocked: bool
    term: str | None = None

    @property
    def reason(self) -> str:
        if not self.blocked:
            return "1차 금칙어 통과"
        return f"1차 금칙어에 '{self.term}' 포함"


def find_blocked(*texts: str) -> str | None:
    """금칙어가 있으면 걸린 원본 단어를, 없으면 None. Claude 호출보다 먼저 부른다."""
    haystack = _normalize(" ".join(t for t in texts if t))
    for original, normalized in _NORMALIZED_TERMS:
        if normalized and normalized in haystack:
            return original
    return None


def check(*texts: str) -> BlockResult:
    """find_blocked 를 BlockResult 로 감싼 형태."""
    term = find_blocked(*texts)
    return BlockResult(blocked=term is not None, term=term)
