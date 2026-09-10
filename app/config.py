"""환경변수 로딩. 모든 설정은 여기서만 읽는다."""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-5"


class Settings(BaseModel):
    anthropic_api_key: str | None
    anthropic_model: str
    cors_origins: list[str]
    data_retention_days: int

    @property
    def ai_enabled(self) -> bool:
        """실제 Claude 호출이 가능한 상태인지."""
        return bool(self.anthropic_api_key)


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
    )
