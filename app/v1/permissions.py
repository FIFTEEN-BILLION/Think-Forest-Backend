"""보호자 권한 확인. 로그인 계정과 아이 프로필의 연결 관계로만 판단한다(URL 의 id 를 믿지 않는다).

권한 키는 명세 16절과 같다. B1 트랙이 guardian_links 테이블로 확장한다.
"""

from __future__ import annotations

from typing import Literal

Permission = Literal["VIEW_PROFILE", "VIEW_STORIES", "VIEW_REPORTS", "REVIEW_SHARING", "MANAGE_DATA"]
ALL_PERMISSIONS: tuple[Permission, ...] = (
    "VIEW_PROFILE",
    "VIEW_STORIES",
    "VIEW_REPORTS",
    "REVIEW_SHARING",
    "MANAGE_DATA",
)
