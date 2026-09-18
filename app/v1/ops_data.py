"""내 데이터 열람·내보내기·삭제의 실제 일(명세 22절).

- 내보내기는 요청 안에서 JSON 을 만들어 `data_jobs.payload` 에 둔다. 파일 시스템을 쓰지 않는다(서버리스).
- 내려받기는 짧게 살고 한 번만 쓰는 토큰으로 한다. 토큰도 해시로만 저장한다.
- 삭제는 유예기간이 지난 뒤 실제로 지운다. 파생 데이터(이야기 정리본, 공개 복사본, 내보내기 파일)까지 포함한다.
- 감사 기록은 `deletion_requests` 행 자체다. 요청자·대상 ID·시각·처리 결과(개수)만 남고 아이 원문은 남지 않는다.
- 실행할 스케줄러가 없다. 내 데이터 API 를 부를 때마다 기한이 지난 요청을 처리한다(`run_due`).
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import clock
from ..auth import hash_token
from ..config import get_settings
from ..models import (
    Book,
    Child,
    SharedItem,
    Story,
    Talk,
    Turn,
    Word,
    WordQuiz,
)
from . import cursor
from .models import AccessToken, User
from .models_conversation import (
    ChildProfile,
    ConversationFact,
    ConversationMessage,
    ConversationSession,
    StoryRecord,
    UserTopic,
)
from .models_ops import DataJob, DeletionRequest, Notification, NotificationSetting, PushDevice

DOWNLOAD_PREFIX = "dlt_"
SCOPES = ("ALL_CHILD_DATA", "CONVERSATIONS", "STORIES", "WORDBOOK", "EXPORTS")
SECTIONS = ("PROFILE", "CONVERSATIONS", "STORIES", "WORDBOOK", "REPORTS")
REASONS = ("USER_REQUEST", "NO_LONGER_USED", "PRIVACY_CONCERN", "OTHER")


# ---------------------------------------------------------------- 열람


def counts(db: Session, user: User, child: Child) -> dict[str, int]:
    def count(model, *where) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    sessions = select(ConversationSession.id).where(ConversationSession.user_id == user.id)
    return {
        "profiles": count(ChildProfile, ChildProfile.user_id == user.id),
        "conversations": count(ConversationSession, ConversationSession.user_id == user.id),
        "messages": count(ConversationMessage, ConversationMessage.session_id.in_(sessions)),
        "stories": count(StoryRecord, StoryRecord.user_id == user.id),
        "topics": count(UserTopic, UserTopic.user_id == user.id),
        "words": count(Word, Word.child_id == child.id),
        "sharedItems": count(SharedItem, SharedItem.child_id == child.id),
        "notifications": count(Notification, Notification.user_id == user.id),
        "exports": count(DataJob, DataJob.user_id == user.id),
    }


def retention() -> dict[str, int]:
    config = get_settings()
    return {
        "conversationDays": config.data_retention_days,
        "exportDownloadMinutes": config.data_export_download_ttl_seconds // 60,
        "deletionGraceDays": config.data_deletion_grace_days,
        "accountDeletionGraceDays": config.account_deletion_grace_days,
    }


# ---------------------------------------------------------------- 내보내기


def build_export(db: Session, user: User, child: Child, include: list[str]) -> dict:
    """내보낼 JSON 을 만든다. 아이 원문은 여기서만 모이고 작업 행에만 저장된다."""
    wanted = set(include or SECTIONS)
    payload: dict = {
        "exportedAt": cursor.iso(clock.now()),
        "userId": user.id,
        "include": sorted(wanted),
        "schemaVersion": 1,
    }
    if "PROFILE" in wanted:
        profile = db.scalar(select(ChildProfile).where(ChildProfile.user_id == user.id))
        payload["profile"] = (
            {
                "id": profile.id,
                "nickname": profile.nickname,
                "schoolOrGroup": profile.school_or_group,
                "gradeOrAgeBand": profile.grade_or_age_band,
                "interests": profile.interests,
                "growthGoal": profile.growth_goal,
                "completedAt": cursor.iso(profile.completed_at),
            }
            if profile
            else None
        )
    if "CONVERSATIONS" in wanted:
        sessions = list(
            db.scalars(
                select(ConversationSession)
                .where(ConversationSession.user_id == user.id)
                .order_by(ConversationSession.created_at)
            )
        )
        payload["conversations"] = [
            {
                "id": s.id,
                "kind": s.kind,
                "topic": s.topic,
                "status": s.status,
                "createdAt": cursor.iso(s.created_at),
                "completedAt": cursor.iso(s.completed_at),
                "messages": [
                    {
                        "id": m.id,
                        "role": m.role,
                        "source": m.source,  # child|ai|fallback — 누가 쓴 문장인지 구분(명세 27절)
                        "text": m.content,
                        "createdAt": cursor.iso(m.created_at),
                    }
                    for m in db.scalars(
                        select(ConversationMessage)
                        .where(ConversationMessage.session_id == s.id)
                        .order_by(ConversationMessage.seq)
                    )
                ],
            }
            for s in sessions
        ]
    if "STORIES" in wanted:
        payload["stories"] = [
            {
                "id": s.id,
                "title": s.title,
                "summary": s.summary,
                "body": s.body,
                "category": s.category,
                "thoughtJourney": s.thought_journey,
                "source": s.source,
                "createdAt": cursor.iso(s.created_at),
            }
            for s in db.scalars(
                select(StoryRecord).where(StoryRecord.user_id == user.id).order_by(StoryRecord.created_at)
            )
        ]
    if "WORDBOOK" in wanted:
        payload["wordbook"] = [
            {
                "word": w.word,
                "meaning": w.meaning,
                "example": w.example,
                "createdAt": cursor.iso(w.created_at),
            }
            for w in db.scalars(select(Word).where(Word.child_id == child.id).order_by(Word.created_at))
        ]
    if "REPORTS" in wanted:
        # 성장 리포트는 저장하지 않고 조회할 때 계산한다(B4 트랙). 내보낼 원본이 없다.
        payload["reports"] = []
    return payload


def issue_download(db: Session, job: DataJob) -> tuple[str, str]:
    """(토큰 원문, 만료 ISO). 한 번 쓰면 사라지고, 만료되면 다음 조회에서 새로 만든다."""
    raw = f"{DOWNLOAD_PREFIX}{secrets.token_urlsafe(24)}"
    job.download_token_hash = hash_token(raw)
    job.download_expires_at = clock.now() + timedelta(seconds=get_settings().data_export_download_ttl_seconds)
    job.downloaded_at = None
    return raw, cursor.iso(job.download_expires_at) or ""


def take_download(db: Session, job_id: str, raw: str | None) -> DataJob:
    """1회용 토큰을 소비한다. 틀리거나 만료·이미 쓴 토큰이면 None 대신 예외를 던진다."""
    job = db.get(DataJob, job_id)
    if job is None or not raw or job.download_token_hash != hash_token(raw):
        raise DownloadInvalid
    if job.download_expires_at is None or job.download_expires_at <= clock.now():
        raise DownloadInvalid
    job.download_token_hash = None
    job.download_expires_at = None
    job.downloaded_at = clock.now()
    db.commit()
    return job


class DownloadInvalid(Exception):
    """없거나·만료됐거나·이미 쓴 내려받기 토큰."""


# ---------------------------------------------------------------- 삭제


def hidden_scopes(db: Session, user_id: str) -> set[str]:
    """삭제를 요청해 지금 숨겨야 하는 범위. 다른 트랙도 이 함수로 확인한다."""
    rows = db.scalars(
        select(DeletionRequest).where(
            DeletionRequest.user_id == user_id,
            DeletionRequest.status.in_(("QUEUED", "RUNNING")),
            DeletionRequest.hidden_at.is_not(None),
        )
    )
    scopes: set[str] = set()
    for row in rows:
        scopes.add("ALL_CHILD_DATA" if row.kind == "ACCOUNT" else row.scope)
    return scopes


def _delete_conversations(db: Session, user: User, child: Child) -> dict[str, int]:
    session_ids = select(ConversationSession.id).where(ConversationSession.user_id == user.id)
    result = {
        "messages": _delete(db, ConversationMessage, ConversationMessage.session_id.in_(session_ids)),
        "facts": _delete(db, ConversationFact, ConversationFact.session_id.in_(session_ids)),
    }
    # 이야기 정리본은 세션을 참조한다. 세션보다 먼저 지운다.
    result["stories"] = _delete(db, StoryRecord, StoryRecord.user_id == user.id)
    result["conversations"] = _delete(db, ConversationSession, ConversationSession.user_id == user.id)
    # 기존 대화 엔진 쪽 기록도 같은 아이 것이다.
    talk_ids = select(Talk.id).where(Talk.child_id == child.id)
    result["turns"] = _delete(db, Turn, Turn.talk_id.in_(talk_ids))
    result["legacyStories"] = _delete(db, Story, Story.child_id == child.id)
    result["talks"] = _delete(db, Talk, Talk.child_id == child.id)
    return result


def _delete_stories(db: Session, user: User, child: Child) -> dict[str, int]:
    return {
        "stories": _delete(db, StoryRecord, StoryRecord.user_id == user.id),
        "legacyStories": _delete(db, Story, Story.child_id == child.id),
        "books": _delete(db, Book, Book.child_id == child.id),
        # 공개 복사본까지 지운다(명세 22절).
        "sharedItems": _delete(db, SharedItem, SharedItem.child_id == child.id),
    }


def _delete_wordbook(db: Session, child: Child) -> dict[str, int]:
    return {
        "words": _delete(db, Word, Word.child_id == child.id),
        "wordQuizzes": _delete(db, WordQuiz, WordQuiz.child_id == child.id),
    }


def _delete_exports(db: Session, user: User) -> dict[str, int]:
    """내보내기 작업에는 아이 원문이 담겨 있다. 함께 지운다."""
    return {"exports": _delete(db, DataJob, DataJob.user_id == user.id)}


def _delete(db: Session, model, *where) -> int:
    return int(db.execute(delete(model).where(*where)).rowcount or 0)


def _scrub_child(db: Session, child: Child) -> None:
    """아이 행은 기존 엔진이 참조한다. 행을 지우는 대신 개인정보를 비운다."""
    child.nickname = ""
    child.grade = None
    child.affiliation = None
    child.likes = []
    child.want_to_learn = []
    child.onboarding = {}
    child.profile_confirmed = False


def execute(db: Session, request: DeletionRequest) -> DeletionRequest:
    """유예기간이 지난 요청을 실제로 처리한다. 지운 개수만 남긴다."""
    user = db.get(User, request.user_id)
    child = db.get(Child, user.child_id) if user else None
    if user is None or child is None:
        request.status = "FAILED"
        request.completed_at = clock.now()
        return request
    request.status = "RUNNING"
    result: dict[str, int] = {}
    scope = "ALL_CHILD_DATA" if request.kind == "ACCOUNT" else request.scope
    if scope in ("ALL_CHILD_DATA", "CONVERSATIONS"):
        result |= _delete_conversations(db, user, child)
    if scope in ("ALL_CHILD_DATA", "STORIES"):
        for key, value in _delete_stories(db, user, child).items():
            result[key] = result.get(key, 0) + value
    if scope in ("ALL_CHILD_DATA", "WORDBOOK"):
        result |= _delete_wordbook(db, child)
    if scope in ("ALL_CHILD_DATA", "EXPORTS"):
        result |= _delete_exports(db, user)
    if scope == "ALL_CHILD_DATA":
        result["topics"] = _delete(db, UserTopic, UserTopic.user_id == user.id)
        result["profiles"] = _delete(db, ChildProfile, ChildProfile.user_id == user.id)
        _scrub_child(db, child)
    if request.kind == "ACCOUNT":
        result |= _close_account(db, user)
    request.status = "SUCCEEDED"
    request.completed_at = clock.now()
    request.result = result  # JSON 컬럼은 새 객체로 바꿔 넣는다
    return request


def _close_account(db: Session, user: User) -> dict[str, int]:
    """계정 탈퇴 — 기기·알림·토큰을 지우고 사용자를 DELETED 로 둔다.

    보호자 계정이면 다른 보호자가 그 아이에 연결돼 있는지 먼저 본다(B1 트랙의 guardian_links).
    지금은 계정 하나에 아이 하나라 다른 연결이 없다.
    """
    counts = {
        "devices": _delete(db, PushDevice, PushDevice.user_id == user.id),
        "notifications": _delete(db, Notification, Notification.user_id == user.id),
        "notificationSettings": _delete(db, NotificationSetting, NotificationSetting.user_id == user.id),
    }
    now = clock.now()
    for token in db.scalars(select(AccessToken).where(AccessToken.user_id == user.id)):
        token.revoked_at = token.revoked_at or now
    user.status = "DELETED"
    return counts


DUE_BATCH = 20


def run_due(db: Session) -> None:
    """기한이 지난 삭제 요청을 처리한다. 내 데이터 API 진입점마다 부른다.

    작업 스케줄러가 없어서 요청을 처리하는 김에 함께 돌린다. 그래서 요청한 사람이 다시 오지 않아도
    다른 요청이 들어오면 삭제가 진행된다. 한 번에 `DUE_BATCH` 건까지만 본다(응답이 길어지지 않게).
    운영에 워커가 생기면 이 함수를 주기적으로 부르면 된다.
    """
    due = list(
        db.scalars(
            select(DeletionRequest)
            .where(DeletionRequest.status == "QUEUED", DeletionRequest.effective_at <= clock.now())
            .order_by(DeletionRequest.effective_at)
            .limit(DUE_BATCH)
        )
    )
    if not due:
        return
    for request in due:
        try:
            execute(db, request)
        except Exception:  # noqa: BLE001 — 한 건이 막혀도 나머지를 계속 처리한다
            db.rollback()
            request = db.get(DeletionRequest, request.id)
            if request is not None:
                request.status = "FAILED"
                request.completed_at = clock.now()
    db.commit()
