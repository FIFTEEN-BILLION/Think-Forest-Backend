"""계정 아래 여러 아이의 대화·책장 범위. 연결이 없는 이전 기록은 기존 계정 범위 유지."""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models_conversation import ConversationOwner


def bind(db: Session, session_id: str, child_id: str) -> None:
    db.add(ConversationOwner(session_id=session_id, child_id=child_id))
    db.flush()


def condition(session_column, child_id: str):
    owned = select(ConversationOwner.session_id).where(ConversationOwner.child_id == child_id)
    mapped = select(ConversationOwner.session_id)
    return or_(session_column.in_(owned), ~session_column.in_(mapped))


def belongs(db: Session, session_id: str, child_id: str) -> bool:
    owner = db.get(ConversationOwner, session_id)
    return owner is None or owner.child_id == child_id
