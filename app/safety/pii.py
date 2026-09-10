"""저장 직전 개인식별정보 마스킹.

아이 발화는 저장 직전에 반드시 여기를 지난다. 가린 항목의 '종류'만 기록하고
원문 식별자는 남기지 않는다. 전화번호·학교/기관명·집 주소·생년월일·이름 패턴.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MASK = "●●●"

# 전화번호: 010-1234-5678, 01012345678, 02-123-4567 등
# 한글이 숫자에 붙는 경우가 많아 \b 대신 숫자 경계 룩어라운드를 쓴다.
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")
# 생년월일: 2018-03-01, 2018.3.1, 2018년 3월 1일, 180301
_BIRTH = re.compile(
    r"(?<!\d)(19|20)\d{2}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?"
    r"|(?<!\d)\d{6}(?!\d)"
)
# 학교·기관명: '○○초등학교', '△△유치원', '□□어린이집', '◇◇학원'
_SCHOOL = re.compile(r"[가-힣A-Za-z0-9]{1,10}(초등학교|중학교|고등학교|유치원|어린이집|학원|학교)")
# 집 주소: '서울시 ...구 ...동', '경기도 ...시 ...읍', '... 아파트 101동 1001호', 번지 표기
_ADDRESS = re.compile(
    r"[가-힣]{2,10}(?:특별시|광역시|특별자치시|특별자치도|시|도)"
    r"\s*[가-힣]{1,10}(?:시|군|구)"
    r"\s*[가-힣]{1,12}(?:동|읍|면|리|가|로|길)(?:\s*\d{1,4}(?:번지|번길|-\d{1,4})?)?"
    r"|[가-힣A-Za-z0-9]+(?:아파트|빌라|맨션|타운|힐스|캐슬)\s*\d{1,4}동\s*\d{1,4}호"
    r"|(?<!\d)\d{1,4}-\d{1,4}번지"
)
# 이름 패턴: '홍길동입니다', '내 이름은 홍길동', '나는 김OO'
_NAME = re.compile(
    r"(?:내?\s*이름은?|나는|저는|제?\s*이름은?)\s*([가-힣]{2,4})(?=\s|$|이에요|예요|입니다|이야|야|이고|고)"
    r"|([가-힣]{2,4})(?:입니다|이에요|예요|이라고\s*해)"
)


@dataclass
class MaskResult:
    text: str
    categories: list[str]  # 가려진 항목의 '종류'만


def mask(text: str) -> MaskResult:
    if not text:
        return MaskResult(text=text, categories=[])

    categories: list[str] = []
    out = text

    def _sub(pattern: re.Pattern[str], label: str, value: str) -> None:
        nonlocal out
        if pattern.search(value):
            out = pattern.sub(_MASK, out)
            categories.append(label)

    # 순서 주의: 주소/학교를 먼저, 그다음 생년월일(6자리 숫자), 전화번호, 이름.
    _sub(_ADDRESS, "집주소", out)
    _sub(_SCHOOL, "학교·기관명", out)
    _sub(_BIRTH, "생년월일", out)
    _sub(_PHONE, "전화번호", out)

    # 이름은 캡처그룹만 치환
    if _NAME.search(out):
        out = _NAME.sub(lambda m: m.group(0).replace(m.group(1) or m.group(2), _MASK), out)
        categories.append("이름")

    return MaskResult(text=out, categories=sorted(set(categories)))
