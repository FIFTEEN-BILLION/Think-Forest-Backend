"""가족·아이·권한·모임·첫 만남 대화 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import CamelModel

Affiliation = Literal["elementary", "homeschool", "other"]


class FamilyCreateResponse(CamelModel):
    family_id: str
    guardian_token: str = Field(description="한 번만 보여 준다. 서버는 해시만 저장한다")
    notice: str


class ChildCreateRequest(CamelModel):
    nickname: str = Field(default="", max_length=10)
    tester: bool = Field(default=False, description="성인 테스터 계정. demo 모드에서도 실제 AI 사용")


class Permissions(CamelModel):
    voice: bool = False
    browse_shared: bool = False
    publish_request: bool = False


class ChildOut(CamelModel):
    id: str
    nickname: str
    grade: int | None
    affiliation: Affiliation | None
    likes: list[str]
    want_to_learn: list[str]
    profile_confirmed: bool
    tester: bool
    permissions: Permissions


class DeviceTokenResponse(CamelModel):
    child_id: str
    child_token: str
    notice: str


class SafetyEventOut(CamelModel):
    category: str
    escalate: bool
    talk_id: str | None
    created_at: datetime


class CircleCreateRequest(CamelModel):
    name: str = Field(min_length=1, max_length=30)


class CircleJoinRequest(CamelModel):
    code: str = Field(min_length=4, max_length=12)


class CircleOut(CamelModel):
    id: str
    name: str
    code: str


# --- 첫 만남 대화 -------------------------------------------------------------


class ProfileDraft(CamelModel):
    nickname: str | None = None
    grade: int | None = None
    affiliation: Affiliation | None = None
    likes: list[str] = Field(default_factory=list)
    want_to_learn: list[str] = Field(default_factory=list)


class OnboardingMessage(CamelModel):
    text: str = Field(min_length=1, max_length=500)
    input_mode: Literal["text", "voice"] = "text"


class OnboardingState(CamelModel):
    ai: bool
    error: str | None = None
    reply: str
    profile: ProfileDraft
    missing: list[str]
    done: bool
    notes: list[str] = Field(default_factory=list, description="예: school_name_not_saved")


class ProfileConfirmRequest(CamelModel):
    nickname: str | None = Field(default=None, max_length=10)
    grade: int | None = Field(default=None, ge=1, le=6)
    affiliation: Affiliation | None = None
    likes: list[str] | None = Field(default=None, max_length=5)
    want_to_learn: list[str] | None = Field(default=None, max_length=5)


class OnboardingLLM(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str | None
    grade: int | None
    affiliation: Affiliation | None
    likes: list[str]
    want_to_learn: list[str]
    reply: str
