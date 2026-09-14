"""환경변수 로딩. 모든 설정은 여기서만 읽는다."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-5"
# 비용 우선 기본값(사용자 결정). flagship 이 필요하면 OPENAI_MODEL 로만 바꾼다.
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"


class Settings(BaseModel):
    anthropic_api_key: str | None
    anthropic_model: str
    cors_origins: list[str]
    data_retention_days: int
    # 첫 탐구 사고력 엔진(OpenAI)
    openai_api_key: str | None = None
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_reasoning_effort: str | None = None
    openai_timeout_s: float = 20.0
    # demo: 예시·성인 입력만. child: ZDR 승인 뒤에만 켠다(실제 아동 입력 허용).
    child_data_mode: Literal["demo", "child"] = "demo"
    # AI_ENABLED=false 로 사고력 엔진의 AI 호출을 즉시 끈다(규칙 기반으로 계속).
    ai_switch: bool = True
    # 생각 친구 대화 엔진
    database_url: str = "sqlite:///./data/thinkforest.db"
    openai_transcribe_model: str = "gpt-transcribe"
    openai_realtime_model: str = "gpt-live-transcribe"
    moderation_model: str = "omni-moderation-latest"
    speech_enabled: bool = True
    daily_ai_call_limit: int = 300
    talk_min_seconds: int = 900  # 한 이야기 필수 15분(실제 대화한 시간 기준)

    @property
    def ai_enabled(self) -> bool:
        """실제 Claude 호출이 가능한 상태인지."""
        return bool(self.anthropic_api_key)

    @property
    def openai_enabled(self) -> bool:
        """사고력 엔진의 실제 OpenAI 호출이 가능한 상태인지."""
        return bool(self.openai_api_key) and self.ai_switch


def _split_origins(raw: str | None) -> list[str]:
    if not raw:
        # CLAUDE.md 절대 규칙: allow_origins=["*"] 금지. 미설정 시 로컬 개발 주소만.
        return ["http://localhost:5173"]
    return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL,
        cors_origins=_split_origins(os.getenv("CORS_ORIGINS")),
        data_retention_days=int(os.getenv("DATA_RETENTION_DAYS") or 90),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
        openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT") or None,
        openai_timeout_s=float(os.getenv("OPENAI_TIMEOUT_S") or 20),
        child_data_mode="child" if os.getenv("CHILD_DATA_MODE") == "child" else "demo",
        ai_switch=_flag(os.getenv("AI_ENABLED")),
        database_url=os.getenv("DATABASE_URL") or "sqlite:///./data/thinkforest.db",
        openai_transcribe_model=os.getenv("OPENAI_TRANSCRIBE_MODEL") or "gpt-transcribe",
        openai_realtime_model=os.getenv("OPENAI_REALTIME_MODEL") or "gpt-live-transcribe",
        moderation_model=os.getenv("OPENAI_MODERATION_MODEL") or "omni-moderation-latest",
        speech_enabled=_flag(os.getenv("SPEECH_ENABLED")),
        daily_ai_call_limit=int(os.getenv("DAILY_AI_CALL_LIMIT") or 300),
        talk_min_seconds=int(os.getenv("TALK_MIN_SECONDS") or 900),
    )


def _flag(raw: str | None, default: bool = True) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")
