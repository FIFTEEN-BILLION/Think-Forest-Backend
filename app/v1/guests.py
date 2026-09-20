"""공개 체험 계정: 방문자별 소유권, 고정 만료, 서버 권한 및 DB 사용량 제한."""

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .. import clock
from .errors import ApiError
from .models import User
from .models_auth import GuestRateLimit

TTL_SECONDS = 24 * 3600
# 프로필·소유권 검사는 각 API의 기존 require_profile/own_session을 그대로 거친다.
CORE_ROOTS = frozenset({
    "me", "home", "profiles", "first-greeting", "conversations", "topics", "topic-categories",
    "activities", "activity-sessions", "stories", "wordbook", "word-quizzes", "books", "reports",
})
READ_ROOTS = frozenset({"community", "notifications", "notification-settings", "legal-documents", "service-info"})
CONSENT_DOCUMENTS = ("privacy_child", "ai_conversation")


def seconds_left(user: User) -> int:
    return max(0, int((user.created_at + timedelta(seconds=TTL_SECONDS) - clock.now()).total_seconds()))


def consume(db: Session, key: str, limit: int) -> None:
    """조건부 UPSERT로 동시 요청에도 한도를 넘지 않는다. 호출자가 commit한다."""
    window = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    stmt = insert(GuestRateLimit).values(key=key, window=window, count=1)
    claimed = db.scalar(stmt.on_conflict_do_update(
        index_elements=[GuestRateLimit.key, GuestRateLimit.window],
        set_={"count": GuestRateLimit.count + 1},
        where=GuestRateLimit.count < limit,
    ).returning(GuestRateLimit.count))
    if claimed is None:
        raise ApiError(429, "GUEST_LIMIT_REACHED", "오늘의 체험 이용량을 모두 사용했어요. 내일 다시 체험해 주세요.")


def check_access(db: Session, user: User, path: str, method: str) -> None:
    if seconds_left(user) <= 0:
        raise ApiError(401, "GUEST_EXPIRED", "체험 기간이 끝났어요. 새 체험을 시작해 주세요.")
    route = path.removeprefix("/api/v1/").strip("/")
    root = route.split("/", 1)[0]
    if route == "auth/logout":
        return
    allowed = root in CORE_ROOTS or root == "consents" or (root in READ_ROOTS and method == "GET")
    if root == "profiles" and method == "POST":
        allowed = False
    if "share-requests" in route.split("/"):
        allowed = False
    if not allowed:
        raise ApiError(403, "GUEST_RESTRICTED", "이 기능은 카카오 로그인 후 사용할 수 있어요.")
    if method not in {"GET", "HEAD", "OPTIONS"}:
        consume(db, f"write:{user.id}", 300)
        consume(db, "write:global", 10000)
        db.commit()


def purge_expired(db: Session, *, limit: int = 10) -> int:
    """새 체험 시작 시 만료 계정을 소량씩 정리한다. 정기 작업에서도 호출할 수 있다."""
    from .debug_reset import erase_account

    users = list(db.scalars(select(User).where(
        User.role == "GUEST", User.created_at <= clock.now() - timedelta(seconds=TTL_SECONDS),
    ).order_by(User.created_at).limit(limit)))
    for user in users:
        erase_account(db, user)
    db.execute(delete(GuestRateLimit).where(GuestRateLimit.window < clock.now() - timedelta(days=2)))
    return len(users)
