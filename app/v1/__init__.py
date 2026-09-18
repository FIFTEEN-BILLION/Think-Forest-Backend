"""JJCP API v1 (`/api/v1`) — 카카오 로그인·티키와 첫인사·티키와 이야기 MVP.

명세: API_SPEC.md(2026-09-17). 기존 경로(/talks, /onboarding 등)는 그대로 두고, 같은 대화 엔진·안전 필터를
이 아래에서 재사용한다. 오류는 {"error": {code, message, details, requestId}} 형식으로 통일한다.
"""

from __future__ import annotations

from fastapi import APIRouter

from .routers import (
    activities,
    admin,
    admin_topics,
    auth,
    books,
    community,
    consents,
    conversations,
    data_rights,
    first_greeting,
    guardian_links,
    home,
    notifications,
    path_sessions,
    profiles,
    reports,
    sharing,
    speech_v1,
    stories,
    topic_categories,
    topics,
    wordbook,
)

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(first_greeting.router)
router.include_router(conversations.router)
router.include_router(topics.router)
router.include_router(home.router)
router.include_router(stories.router)
# 계정·프로필·동의
router.include_router(profiles.router)
router.include_router(guardian_links.router)
router.include_router(consents.router)
# 책장·단어·이야기책
router.include_router(wordbook.router)
router.include_router(books.router)
# 공유·커뮤니티·리포트·운영
router.include_router(sharing.router)
router.include_router(community.router)
router.include_router(reports.router)
router.include_router(admin.router)
# 활동·주제 운영
router.include_router(activities.router)
router.include_router(path_sessions.router)
router.include_router(topic_categories.router)
router.include_router(admin_topics.router)
# 음성·알림·내 데이터
router.include_router(speech_v1.router)
router.include_router(notifications.router)
router.include_router(data_rights.router)
