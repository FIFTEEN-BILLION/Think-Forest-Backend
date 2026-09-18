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
    # --- v1 ops ---
    # 음성 스트리밍·알림·내 데이터. Vercel 서버리스는 WebSocket 을 붙잡고 있지 못한다 — Render/Fly 로 따로 올린다.
    # 비우면 요청 주소에서 ws/wss 를 유도한다. WebSocket 을 못 여는 배포에서는 WEBSOCKET_ENABLED=false 로 끈다.
    public_ws_base_url: str = ""
    websocket_enabled: bool = True
    speech_ticket_ttl_seconds: int = 30
    speech_max_utterance_seconds: int = 60  # 한 발화 상한
    speech_warn_utterance_seconds: int = 50  # 이때 부드럽게 마무리를 안내한다
    # 푸시 발송 주소(Expo). 비우면 푸시는 보내지 않고 앱 내 알림만 기록한다.
    expo_push_url: str = ""
    expo_access_token: str | None = None
    data_export_download_ttl_seconds: int = 600
    data_deletion_grace_days: int = 7
    account_deletion_grace_days: int = 30
    # 삭제·탈퇴 요청에 필요한 재인증: 이 시간보다 오래된 access token 이면 다시 로그인하게 한다.
    ops_reauth_max_age_seconds: int = 900
    # --- /v1 ops ---

    @property
    def ai_enabled(self) -> bool:
        """실제 Claude 호출이 가능한 상태인지."""
        return bool(self.anthropic_api_key)

    @property
    def openai_enabled(self) -> bool:
        """사고력 엔진의 실제 OpenAI 호출이 가능한 상태인지."""
        return bool(self.openai_api_key) and self.ai_switch

    # --- v1 ops ---
    @property
    def push_enabled(self) -> bool:
        """실제 푸시 발송이 가능한 상태인지. 아니면 앱 내 알림만 기록한다."""
        return bool(self.expo_push_url)

    # --- /v1 ops ---

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
        # --- v1 ops ---
        public_ws_base_url=(os.getenv("PUBLIC_WS_BASE_URL") or "").rstrip("/"),
        websocket_enabled=_flag(os.getenv("WEBSOCKET_ENABLED")),
        speech_ticket_ttl_seconds=int(os.getenv("SPEECH_TICKET_TTL_SECONDS") or 30),
        speech_max_utterance_seconds=int(os.getenv("SPEECH_MAX_UTTERANCE_SECONDS") or 60),
        speech_warn_utterance_seconds=int(os.getenv("SPEECH_WARN_UTTERANCE_SECONDS") or 50),
        expo_push_url=(os.getenv("EXPO_PUSH_URL") or "").strip(),
        expo_access_token=os.getenv("EXPO_ACCESS_TOKEN") or None,
        data_export_download_ttl_seconds=int(os.getenv("DATA_EXPORT_DOWNLOAD_TTL_SECONDS") or 600),
        data_deletion_grace_days=int(os.getenv("DATA_DELETION_GRACE_DAYS") or 7),
        account_deletion_grace_days=int(os.getenv("ACCOUNT_DELETION_GRACE_DAYS") or 30),
        ops_reauth_max_age_seconds=int(os.getenv("OPS_REAUTH_MAX_AGE_SECONDS") or 900),
        # --- /v1 ops ---
    )


def _flag(raw: str | None, default: bool = True) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")
