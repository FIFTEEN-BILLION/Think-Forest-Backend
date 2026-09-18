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
    # --- v1 conversation ---
    # 티키와 이야기 READY_TO_FINISH 조건: 모든 학습 차원 + 유효 응답 수 + 실제 대화 시간(초)
    conversation_min_responses: int = 6
    conversation_min_seconds: int = 300
    # --- end v1 conversation ---
    # 텍스트 → 음성(Typecast). voice_id 는 콘솔/GET https://api.typecast.ai/v2/voices 에서 확인.
    typecast_api_key: str | None = None
    typecast_voice_id: str | None = None
    typecast_model: str = "ssfm-v30"
    # --- v1 auth ---
    # 카카오 로그인(REST API 키·Redirect URI 가 없으면 authorize·callback·mobile 은 503).
    kakao_rest_api_key: str | None = None
    kakao_client_secret: str | None = None
    kakao_redirect_uri: str | None = None
    # 모바일 토큰 검증 시 access_token_info.app_id 와 비교한다. 운영에서는 반드시 설정한다.
    kakao_app_id: str | None = None
    # 로그인 뒤 돌아갈 프론트 주소. 비우면 같은 출처 상대 경로로 이동한다.
    frontend_base_url: str = ""
    # 개발용 로그인(POST /api/v1/auth/dev/login). 운영에서는 끈다.
    auth_dev_login: bool = False
    # refresh 쿠키 Secure 속성. http 로컬 개발에서만 false.
    auth_cookie_secure: bool = True
    # --- /v1 auth ---
    # --- v1 activities ---
    # 운영자 허용 목록(`/admin/topic-schedules`). 카카오 회원번호를 쉼표로 나눠 적는다.
    # B3 트랙도 같은 키를 쓴다 — 이름을 바꾸지 말 것(ADMIN_KAKAO_IDS).
    admin_kakao_ids: tuple[str, ...] = ()
    # --- /v1 activities ---
    # --- v1 library ---
    # 단어장 복습 간격(일). 상태가 오를수록 다음 복습이 멀어진다.
    wordbook_review_days_new: int = 1
    wordbook_review_days_practicing: int = 3
    wordbook_review_days_familiar: int = 7
    # 단어 퀴즈 한 판의 문항 수(기본·최대)와 보기 수.
    word_quiz_default_count: int = 5
    word_quiz_max_count: int = 10
    word_quiz_option_count: int = 4
    # 이야기책 한 권에 담을 수 있는 이야기 수.
    story_book_max_stories: int = 30
    # --- end v1 library ---

    @property
    def ai_enabled(self) -> bool:
        """실제 Claude 호출이 가능한 상태인지."""
        return bool(self.anthropic_api_key)

    @property
    def openai_enabled(self) -> bool:
        """사고력 엔진의 실제 OpenAI 호출이 가능한 상태인지."""
        return bool(self.openai_api_key) and self.ai_switch

    @property
    def typecast_enabled(self) -> bool:
        """실제 Typecast 합성이 가능한 상태인지(키 + voice_id 모두 필요)."""
        return bool(self.typecast_api_key) and bool(self.typecast_voice_id)


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
        # --- v1 conversation ---
        conversation_min_responses=int(os.getenv("CONVERSATION_MIN_RESPONSES") or 6),
        conversation_min_seconds=int(os.getenv("CONVERSATION_MIN_SECONDS") or 300),
        # --- end v1 conversation ---
        typecast_api_key=os.getenv("TYPECAST_API_KEY") or None,
        typecast_voice_id=os.getenv("TYPECAST_VOICE_ID") or None,
        typecast_model=os.getenv("TYPECAST_MODEL") or "ssfm-v30",
        # --- v1 auth ---
        kakao_rest_api_key=os.getenv("KAKAO_REST_API_KEY") or None,
        kakao_client_secret=os.getenv("KAKAO_CLIENT_SECRET") or None,
        kakao_redirect_uri=os.getenv("KAKAO_REDIRECT_URI") or None,
        kakao_app_id=os.getenv("KAKAO_APP_ID") or None,
        frontend_base_url=(os.getenv("FRONTEND_BASE_URL") or "").rstrip("/"),
        auth_dev_login=_flag(os.getenv("AUTH_DEV_LOGIN"), default=False),
        auth_cookie_secure=_flag(os.getenv("AUTH_COOKIE_SECURE")),
        # --- /v1 auth ---
        # --- v1 activities ---
        admin_kakao_ids=tuple(i.strip() for i in (os.getenv("ADMIN_KAKAO_IDS") or "").split(",") if i.strip()),
        # --- /v1 activities ---
        # --- v1 library ---
        wordbook_review_days_new=int(os.getenv("WORDBOOK_REVIEW_DAYS_NEW") or 1),
        wordbook_review_days_practicing=int(os.getenv("WORDBOOK_REVIEW_DAYS_PRACTICING") or 3),
        wordbook_review_days_familiar=int(os.getenv("WORDBOOK_REVIEW_DAYS_FAMILIAR") or 7),
        word_quiz_default_count=int(os.getenv("WORD_QUIZ_DEFAULT_COUNT") or 5),
        word_quiz_max_count=int(os.getenv("WORD_QUIZ_MAX_COUNT") or 10),
        word_quiz_option_count=int(os.getenv("WORD_QUIZ_OPTION_COUNT") or 4),
        story_book_max_stories=int(os.getenv("STORY_BOOK_MAX_STORIES") or 30),
        # --- end v1 library ---
    )


def _flag(raw: str | None, default: bool = True) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")
