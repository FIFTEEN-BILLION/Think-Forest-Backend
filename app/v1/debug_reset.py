"""현재 계정 초기화. 기본 카탈로그·주제 편성·다른 계정은 유지한다.

실제 외래 키와 레거시의 논리 참조를 따라 삭제 대상을 먼저 확정한 뒤,
하나의 트랜잭션에서 자식부터 삭제한다. commit은 라우터가 수행한다.
"""

from sqlalchemy import delete, or_, select, tuple_
from sqlalchemy.orm import Session

from ..db import Base
from .errors import ApiError
from .models import User

# 검토한 사용자 테이블만 삭제한다. 새 전역 테이블이 생겨도 자동으로 삭제 범위에 들어오지 않는다.
# 기본 콘텐츠(코드 상수)·topic_schedules·사용자 연결이 없는 oauth_states는 보존한다.
USER_TABLES = set(
    [
        "families",
        "children",
        "users",
        "access_tokens",
        "auth_identities",
        "refresh_sessions",
        "login_idempotency",
        "child_profiles",
        "profile_members",
        "profile_settings",
        "guardian_invitations",
        "consents",
        "conversation_sessions",
        "conversation_messages",
        "conversation_facts",
        "conversation_owners",
        "story_records",
        "idempotency_records",
        "user_topics",
        "activity_sessions",
        "topic_categories_v1",
        "wordbook_entries",
        "word_quizzes_v1",
        "word_quiz_questions",
        "story_books",
        "story_book_items",
        "share_requests_v1",
        "public_stories",
        "story_recommendations",
        "community_reports_v1",
        "consultations_v1",
        "consultation_questions_v1",
        "report_summaries",
        "speech_stream_tickets",
        "push_devices",
        "notifications",
        "notification_settings",
        "data_jobs",
        "deletion_requests",
        "device_tokens",
        "categories",
        "talks",
        "turns",
        "stories",
        "books",
        "words",
        "word_quizzes",
        "circles",
        "circle_members",
        "shared_items",
        "share_reports",
        "safety_events",
        "consultations",
    ]
)
USER_COLUMNS = {"user_id", "author_user_id", "reporter_user_id", "actor_user_id", "inviter_user_id"}
# 현재 스키마에서 FK 없이 저장한 관계. 참조 대상을 지울 때 관련 기록도 지운다.
LOGICAL_REFERENCES = {
    ("conversation_sessions", "id"): ("conversation_owners", "session_id"),
    ("public_stories", "story_id"): ("story_records", "id"),
    ("public_stories", "share_request_id"): ("share_requests_v1", "id"),
    ("story_recommendations", "public_story_id"): ("public_stories", "id"),
    ("community_reports_v1", "public_story_id"): ("public_stories", "id"),
    ("consultation_questions_v1", "consultation_id"): ("consultations_v1", "id"),
}


def erase_account(db: Session, user: User) -> None:
    tables = Base.metadata.tables
    children = tables["children"]
    profiles = tables["child_profiles"]
    child_ids = list(db.scalars(select(children.c.id).where(children.c.family_id == user.family_id)))
    profile_ids = list(db.scalars(select(profiles.c.id).where(profiles.c.child_id.in_(child_ids))))
    members = tables["profile_members"]
    circles = tables["circles"]
    circle_members = tables["circle_members"]
    circle_ids = select(circles.c.id).where(circles.c.created_by == user.family_id)

    # 공동 사용 중인 아이/가족/모임을 연쇄 삭제하지 않는다. 차단 시 아무 것도 변경하지 않는다.
    shared = (
        db.scalar(
            select(User.id)
            .where(
                User.id != user.id,
                or_(User.family_id == user.family_id, User.child_id.in_(child_ids)),
            )
            .limit(1)
        )
        or db.scalar(
            select(profiles.c.id)
            .where(
                profiles.c.child_id.in_(child_ids),
                profiles.c.user_id != user.id,
            )
            .limit(1)
        )
        or db.scalar(
            select(members.c.id)
            .where(
                members.c.profile_id.in_(profile_ids),
                members.c.user_id != user.id,
                members.c.revoked_at.is_(None),
            )
            .limit(1)
        )
        or db.scalar(
            select(circle_members.c.circle_id)
            .where(
                circle_members.c.circle_id.in_(circle_ids),
                circle_members.c.family_id != user.family_id,
            )
            .limit(1)
        )
    )
    if shared:
        raise ApiError(409, "SHARED_DATA", "다른 계정과 공유 중인 프로필이나 모임이 있어 초기화할 수 없어요.")

    targets = {name: set() for name in tables if name in USER_TABLES}
    targets["users"].add((user.id,))
    targets["families"].add((user.family_id,))
    # 참조가 여러 단계를 거쳐도 삭제 전에 전체 집합을 확정한다.
    changed = True
    while changed:
        changed = False
        for name, selected in targets.items():
            table = tables[name]
            conditions = [table.c[col] == user.id for col in USER_COLUMNS if col in table.c]
            if "profile_id" in table.c:
                conditions.append(table.c.profile_id.in_(profile_ids))
            if "child_id" in table.c and name != "users":
                conditions.append(table.c.child_id.in_(child_ids))
            references = [(fk.parent.name, fk.column.table.name, fk.column.name) for fk in table.foreign_keys]
            references += [
                (column, parent, key)
                for (source, column), (parent, key) in LOGICAL_REFERENCES.items()
                if source == name
            ]
            for column, parent, key in references:
                parent_rows = targets.get(parent)
                if not parent_rows:
                    continue
                parent_keys = list(tables[parent].primary_key.columns.keys())
                index = parent_keys.index(key)
                conditions.append(table.c[column].in_([row[index] for row in parent_rows]))
            if not conditions:
                continue
            rows = {tuple(row) for row in db.execute(select(*table.primary_key.columns).where(or_(*conditions)))}
            if not rows.issubset(selected):
                selected.update(rows)
                changed = True

    for table in reversed(Base.metadata.sorted_tables):
        rows = targets.get(table.name)
        if rows:
            keys = list(table.primary_key.columns)
            # DB 매개변수 한도를 넘지 않도록 나눠 지운다.
            rows = list(rows)
            for start in range(0, len(rows), 200):
                db.execute(delete(table).where(tuple_(*keys).in_(rows[start : start + 200])))
