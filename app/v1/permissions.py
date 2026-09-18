"""보호자 권한 확인. 로그인 계정과 아이 프로필의 연결 관계로만 판단한다(URL 의 id 를 믿지 않는다).

권한 키는 명세 16절과 같다. 실제 연결 행은 `models_accounts.ProfileMember` 다.
프로필을 만든 계정(OWNER)은 항상 전 권한을 갖고, 초대로 붙은 보호자(GUARDIAN)는 초대에 적힌 권한만 갖는다.
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
# 초대에 권한을 적지 않았을 때 주는 기본값 — 보기만 하고 바꾸지는 못한다.
GUARDIAN_DEFAULT: tuple[Permission, ...] = ("VIEW_PROFILE", "VIEW_STORIES", "VIEW_REPORTS")


def normalize(values: list[str] | None, *, default: tuple[str, ...] = GUARDIAN_DEFAULT) -> list[str]:
    """알 수 없는 권한 키는 400. 순서는 ALL_PERMISSIONS 를 따르고 중복은 지운다."""
    from .errors import ApiError

    if values is None:
        return list(default)
    unknown = [value for value in values if value not in ALL_PERMISSIONS]
    if unknown:
        details = {"fields": ["permissions"], "unknown": unknown}
        raise ApiError(400, "INVALID_INPUT", "권한 값을 확인해 주세요.", details)
    chosen = set(values)
    return [permission for permission in ALL_PERMISSIONS if permission in chosen]


def has(permissions: tuple[str, ...] | list[str], needed: str) -> bool:
    return needed in permissions
