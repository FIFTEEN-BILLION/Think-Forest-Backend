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
from . import conversation_scope, cursor
from .models import AccessToken, User
from .models_accounts import Consent, GuardianInvitation, ProfileMember, ProfileSettings
from .models_activity import ActivitySession, TopicCategoryRow
from .models_conversation import (
    ChildProfile,
    ConversationFact,
    ConversationMessage,
    ConversationOwner,
    ConversationSession,
    StoryRecord,
    UserTopic,
)
from .models_library import StoryBook, StoryBookItem, WordbookEntry, WordQuizQuestion, WordQuizV1
from .models_ops import DataJob, DeletionRequest, Notification, NotificationSetting, PushDevice
from .models_social import (
    CommunityReport,
    ConsultationQuestion,
    GuardianConsultation,
    PublicStory,
    ReportSummary,
    ShareRequest,
    StoryRecommendation,
)

DOWNLOAD_PREFIX = "dlt_"
SCOPES = ("ALL_CHILD_DATA", "CONVERSATIONS", "STORIES", "WORDBOOK", "EXPORTS")
SECTIONS = ("PROFILE", "CONVERSATIONS", "STORIES", "WORDBOOK", "REPORTS")
REASONS = ("USER_REQUEST", "NO_LONGER_USED", "PRIVACY_CONCERN", "OTHER")


# ---------------------------------------------------------------- 열람


def counts(db: Session, user: User, child: Child) -> dict[str, int]:
    def count(model, *where) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == child.id))
    profile_id = profile.id if profile else ""
    sessions = _session_ids(user, child)
    return {
        "profiles": count(ChildProfile, ChildProfile.child_id == child.id),
        "conversations": count(ConversationSession, ConversationSession.id.in_(sessions)),
        "messages": count(ConversationMessage, ConversationMessage.session_id.in_(sessions)),
        "stories": count(StoryRecord, StoryRecord.session_id.in_(sessions)),
        "topics": count(UserTopic, UserTopic.user_id == user.id),
        "words": count(Word, Word.child_id == child.id) + count(WordbookEntry, WordbookEntry.profile_id == profile_id),
        "books": count(StoryBook, StoryBook.profile_id == profile_id),
        "activities": count(ActivitySession, ActivitySession.profile_id == profile_id),
        "reports": count(ReportSummary, ReportSummary.profile_id == profile_id),
        "sharedItems": count(SharedItem, SharedItem.child_id == child.id)
        + count(
            PublicStory, PublicStory.story_id.in_(select(StoryRecord.id).where(StoryRecord.session_id.in_(sessions)))
        ),
        "notifications": count(Notification, Notification.user_id == user.id),
        "exports": count(DataJob, DataJob.user_id == user.id, DataJob.profile_id == (profile.id if profile else None)),
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
        "schemaVersion": 2,
    }
    if "PROFILE" in wanted:
        profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == child.id))
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
                .where(ConversationSession.id.in_(_session_ids(user, child)))
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
                select(StoryRecord)
                .where(StoryRecord.session_id.in_(_session_ids(user, child)))
                .order_by(StoryRecord.created_at)
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
        profile_ids = select(ChildProfile.id).where(ChildProfile.child_id == child.id)
        payload["reports"] = [
            {"id": row.id, "from": row.period_from, "to": row.period_to, "body": row.body, "source": row.source}
            for row in db.scalars(select(ReportSummary).where(ReportSummary.profile_id.in_(profile_ids)))
        ]
        payload["consultations"] = [
            {"id": row.id, "period": row.period, "body": row.body, "source": row.source}
            for row in db.scalars(select(GuardianConsultation).where(GuardianConsultation.profile_id.in_(profile_ids)))
        ]
    profile_ids = select(ChildProfile.id).where(ChildProfile.child_id == child.id)
    if "WORDBOOK" in wanted:
        payload["wordbook"] += [
            {
                "id": w.id,
                "word": w.word,
                "meaning": w.meaning,
                "example": w.example,
                "mySentence": w.my_sentence,
                "status": w.status,
                "createdAt": cursor.iso(w.created_at),
            }
            for w in db.scalars(select(WordbookEntry).where(WordbookEntry.profile_id.in_(profile_ids)))
        ]
    if "STORIES" in wanted:
        payload["books"] = [
            {
                "id": b.id,
                "title": b.title,
                "introduction": b.introduction,
                "cover": b.cover,
                "storyIds": list(
                    db.scalars(
                        select(StoryBookItem.story_id)
                        .where(StoryBookItem.book_id == b.id)
                        .order_by(StoryBookItem.position)
                    )
                ),
            }
            for b in db.scalars(select(StoryBook).where(StoryBook.profile_id.in_(profile_ids)))
        ]
    if "CONVERSATIONS" in wanted:
        payload["activities"] = [
            {"id": a.id, "activityId": a.activity_id, "status": a.status, "state": a.state}
            for a in db.scalars(select(ActivitySession).where(ActivitySession.profile_id.in_(profile_ids)))
        ]
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


def hidden_scopes(db: Session, user_id: str, child_id: str | None = None) -> set[str]:
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
        if child_id is None or row.kind == "ACCOUNT" or row.child_id == child_id:
            scopes.add("ALL_CHILD_DATA" if row.kind == "ACCOUNT" else row.scope)
    return scopes


def _session_ids(user: User, child: Child):
    return select(ConversationSession.id).where(
        ConversationSession.user_id == user.id, conversation_scope.condition(ConversationSession.id, child.id)
    )


def _delete_derived(db: Session, user: User, child: Child) -> dict[str, int]:
    profiles = select(ChildProfile.id).where(ChildProfile.child_id == child.id)
    stories = select(StoryRecord.id).where(StoryRecord.session_id.in_(_session_ids(user, child)))
    public_ids = list(db.scalars(select(PublicStory.id).where(PublicStory.story_id.in_(stories))))
    _delete(db, StoryRecommendation, StoryRecommendation.public_story_id.in_(public_ids))
    _delete(db, CommunityReport, CommunityReport.public_story_id.in_(public_ids))
    _delete(db, PublicStory, PublicStory.id.in_(public_ids))
    _delete(db, ShareRequest, ShareRequest.story_id.in_(stories))
    books = select(StoryBook.id).where(StoryBook.profile_id.in_(profiles))
    _delete(db, StoryBookItem, StoryBookItem.book_id.in_(books) | StoryBookItem.story_id.in_(stories))
    _delete(db, StoryBook, StoryBook.profile_id.in_(profiles))
    consultations = select(GuardianConsultation.id).where(GuardianConsultation.profile_id.in_(profiles))
    _delete(db, ConsultationQuestion, ConsultationQuestion.consultation_id.in_(consultations))
    _delete(db, GuardianConsultation, GuardianConsultation.profile_id.in_(profiles))
    _delete(db, ReportSummary, ReportSummary.profile_id.in_(profiles))
    _delete(db, ActivitySession, ActivitySession.profile_id.in_(profiles))
    _delete_exports(db, user, child)
    return {}


def _delete_conversations(db: Session, user: User, child: Child) -> dict[str, int]:
    session_ids = list(db.scalars(_session_ids(user, child)))
    _delete_derived(db, user, child)
    words = _delete_wordbook(db, child)
    result = {
        **words,
        "messages": _delete(db, ConversationMessage, ConversationMessage.session_id.in_(session_ids)),
        "facts": _delete(db, ConversationFact, ConversationFact.session_id.in_(session_ids)),
    }
    # 이야기 정리본은 세션을 참조한다. 세션보다 먼저 지운다.
    result["stories"] = _delete(db, StoryRecord, StoryRecord.session_id.in_(session_ids))
    _delete(db, ConversationOwner, ConversationOwner.session_id.in_(session_ids))
    result["conversations"] = _delete(db, ConversationSession, ConversationSession.id.in_(session_ids))
    # 기존 대화 엔진 쪽 기록도 같은 아이 것이다.
    talk_ids = select(Talk.id).where(Talk.child_id == child.id)
    result["turns"] = _delete(db, Turn, Turn.talk_id.in_(talk_ids))
    result["legacyStories"] = _delete(db, Story, Story.child_id == child.id)
    result["talks"] = _delete(db, Talk, Talk.child_id == child.id)
    return result


def _delete_stories(db: Session, user: User, child: Child) -> dict[str, int]:
    _delete_derived(db, user, child)
    return {
        "stories": _delete(db, StoryRecord, StoryRecord.session_id.in_(_session_ids(user, child))),
        "legacyStories": _delete(db, Story, Story.child_id == child.id),
        "books": _delete(db, Book, Book.child_id == child.id),
        # 공개 복사본까지 지운다(명세 22절).
        "sharedItems": _delete(db, SharedItem, SharedItem.child_id == child.id),
    }


def _delete_wordbook(db: Session, child: Child) -> dict[str, int]:
    profiles = select(ChildProfile.id).where(ChildProfile.child_id == child.id)
    quizzes = select(WordQuizV1.id).where(WordQuizV1.profile_id.in_(profiles))
    _delete(db, WordQuizQuestion, WordQuizQuestion.quiz_id.in_(quizzes))
    _delete(db, WordQuizV1, WordQuizV1.profile_id.in_(profiles))
    current_words = _delete(db, WordbookEntry, WordbookEntry.profile_id.in_(profiles))
    return {
        "words": current_words + _delete(db, Word, Word.child_id == child.id),
        "wordQuizzes": _delete(db, WordQuiz, WordQuiz.child_id == child.id),
    }


def _delete_exports(db: Session, user: User, child: Child) -> dict[str, int]:
    """내보내기 작업에는 아이 원문이 담겨 있다. 함께 지운다."""
    profile = db.scalar(select(ChildProfile.id).where(ChildProfile.child_id == child.id))
    return {"exports": _delete(db, DataJob, DataJob.user_id == user.id, DataJob.profile_id == profile)}


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


def _erase_child(db: Session, user: User, child: Child, scope: str) -> dict[str, int]:
    result: dict[str, int] = {}
    actions = []
    if scope in ("ALL_CHILD_DATA", "CONVERSATIONS"):
        actions.append(lambda: _delete_conversations(db, user, child))
    if scope in ("ALL_CHILD_DATA", "STORIES"):
        actions.append(lambda: _delete_stories(db, user, child))
    if scope in ("ALL_CHILD_DATA", "WORDBOOK"):
        actions.append(lambda: _delete_wordbook(db, child))
    if scope in ("ALL_CHILD_DATA", "EXPORTS"):
        actions.append(lambda: _delete_exports(db, user, child))
    for action in actions:
        for key, value in action().items():
            result[key] = result.get(key, 0) + value
    if scope == "ALL_CHILD_DATA":
        profiles = select(ChildProfile.id).where(ChildProfile.child_id == child.id)
        for model in (ProfileSettings, ProfileMember, Consent, GuardianInvitation, TopicCategoryRow):
            _delete(db, model, model.profile_id.in_(profiles))
        result["profiles"] = _delete(db, ChildProfile, ChildProfile.child_id == child.id)
        _scrub_child(db, child)
    return result


def execute(db: Session, request: DeletionRequest) -> DeletionRequest:
    """삭제 요청 시점의 아이를 대상으로 처리한다. 기본 프로필 변경이 삭제 대상을 바꾸지 않는다."""
    user = db.get(User, request.user_id)
    child = db.get(Child, request.child_id or user.child_id) if user else None
    if user is None or child is None:
        request.status = "FAILED"
        request.completed_at = clock.now()
        return request
    request.status = "RUNNING"
    result: dict[str, int] = {}
    if request.kind == "ACCOUNT":
        profiles = list(db.scalars(select(ChildProfile).where(ChildProfile.user_id == user.id)))
        children = []
        if not profiles:
            children.append(child)
        for profile in profiles:
            # 다른 보호자가 계속 관리하는 프로필의 기록은 보존한다.
            remaining = db.scalar(
                select(ProfileMember).where(
                    ProfileMember.profile_id == profile.id,
                    ProfileMember.user_id != user.id,
                    ProfileMember.revoked_at.is_(None),
                )
            )
            if remaining:
                result["preservedProfiles"] = result.get("preservedProfiles", 0) + 1
            else:
                target = db.get(Child, profile.child_id)
                if target:
                    children.append(target)
        for target in children:
            for key, value in _erase_child(db, user, target, "ALL_CHILD_DATA").items():
                result[key] = result.get(key, 0) + value
        result["topics"] = _delete(db, UserTopic, UserTopic.user_id == user.id)
        _delete(db, DataJob, DataJob.user_id == user.id)
        result |= _close_account(db, user)
    else:
        result = _erase_child(db, user, child, request.scope)
    request.status = "SUCCEEDED"
    request.completed_at = clock.now()
    request.result = result
    return request


def _close_account(db: Session, user: User) -> dict[str, int]:
    """계정 탈퇴 — 기기·알림·토큰을 지우고 사용자를 DELETED 로 둔다.

    다른 보호자가 연결된 프로필은 execute 에서 보존하고 탈퇴 계정의 연결만 철회한다.
    """
    counts = {
        "devices": _delete(db, PushDevice, PushDevice.user_id == user.id),
        "notifications": _delete(db, Notification, Notification.user_id == user.id),
        "notificationSettings": _delete(db, NotificationSetting, NotificationSetting.user_id == user.id),
    }
    now = clock.now()
    for member in db.scalars(select(ProfileMember).where(ProfileMember.user_id == user.id)):
        member.revoked_at = member.revoked_at or now
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
