"""JJCP API v1 (`/api/v1`) — 카카오 로그인·티키와 첫인사·티키와 이야기 MVP.

명세: API_SPEC.md(2026-09-17). 기존 경로(/talks, /onboarding 등)는 그대로 두고, 같은 대화 엔진·안전 필터를
이 아래에서 재사용한다. 오류는 {"error": {code, message, details, requestId}} 형식으로 통일한다.
"""

from __future__ import annotations

from fastapi import APIRouter

from .routers import auth, conversations, first_greeting, home, stories, topics

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(first_greeting.router)
router.include_router(conversations.router)
router.include_router(topics.router)
router.include_router(home.router)
router.include_router(stories.router)
