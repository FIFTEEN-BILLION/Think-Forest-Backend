"""v1 이야기책 규칙 — 소유 확인, 담긴 이야기 순서, 머리말(AI·규칙), 직렬화.

책은 이야기를 가리키기만 한다. 책을 지워도 이야기(story_records)는 그대로 남는다.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import get_settings
from ..services import usage
from . import ai_gate
from .cursor import iso
from .deps import ProfileScope
from .errors import ApiError
from .library_common import own_story, safe_ai_line, story_summary
from .library_prompts import book_intro_input, book_intro_instructions
from .library_schemas import BookCover, BookDetail, BookIntroLLM, BookSummary
from .models_conversation import StoryRecord
from .models_library import StoryBook, StoryBookItem


def own_book(db: Session, scope: ProfileScope, book_id: str) -> StoryBook:
    book = db.get(StoryBook, book_id)
    if book is None or book.profile_id != scope.profile_id:
        raise ApiError(404, "BOOK_NOT_FOUND", "이야기책을 찾을 수 없어요.")
    return book


def items(db: Session, book_id: str) -> list[StoryBookItem]:
    stmt = select(StoryBookItem).where(StoryBookItem.book_id == book_id).order_by(StoryBookItem.position)
    return list(db.scalars(stmt))


def ensure_draft(book: StoryBook) -> None:
    if book.status == "COMPLETED":
        raise ApiError(409, "BOOK_COMPLETED", "이미 완성한 책이에요.", {"status": book.status})


def touch(book: StoryBook) -> None:
    book.version += 1
    book.updated_at = clock.now()


def renumber(rows: list[StoryBookItem]) -> None:
    for position, row in enumerate(rows):
        row.position = position


def add_story(db: Session, scope: ProfileScope, book: StoryBook, story_id: str, position: int | None) -> None:
    story = own_story(db, scope.user.id, story_id)
    rows = items(db, book.id)
    if any(row.story_id == story.id for row in rows):
        raise ApiError(409, "STORY_ALREADY_IN_BOOK", "이미 이 책에 담긴 이야기예요.", {"storyId": story.id})
    if len(rows) >= get_settings().story_book_max_stories:
        raise ApiError(409, "BOOK_FULL", "이 책에는 더 담을 수 없어요.", {"maxStories": len(rows)})
    row = StoryBookItem(book_id=book.id, story_id=story.id, created_at=clock.now())
    where = len(rows) if position is None else min(position, len(rows))
    rows.insert(where, row)
    db.add(row)
    renumber(rows)


def remove_story(db: Session, book: StoryBook, story_id: str) -> None:
    rows = items(db, book.id)
    row = next((r for r in rows if r.story_id == story_id), None)
    if row is None:
        raise ApiError(404, "STORY_NOT_IN_BOOK", "이 책에 담기지 않은 이야기예요.")
    rows.remove(row)
    db.delete(row)
    db.flush()
    renumber(rows)


def reorder(db: Session, book: StoryBook, story_ids: list[str]) -> None:
    """담긴 이야기들의 순서만 바꾼다. 추가·삭제는 전용 엔드포인트로 한다."""
    rows = items(db, book.id)
    if sorted(story_ids) != sorted(r.story_id for r in rows) or len(set(story_ids)) != len(story_ids):
        raise ApiError(
            400, "INVALID_INPUT", "지금 책에 담긴 이야기들만 순서를 바꿀 수 있어요.", {"fields": ["storyIds"]}
        )
    by_id = {r.story_id: r for r in rows}
    renumber([by_id[sid] for sid in story_ids])


def detach_story(db: Session, story_id: str) -> None:
    """이야기를 지울 때 책에서만 빼낸다(다른 이야기는 건드리지 않는다)."""
    for row in db.scalars(select(StoryBookItem).where(StoryBookItem.story_id == story_id)):
        db.delete(row)
        db.flush()
        renumber(items(db, row.book_id))


# --- 머리말 ---------------------------------------------------------------------


def _fallback_intro(title: str, stories: list[StoryRecord]) -> str:
    if not stories:
        return f"‘{title}’ 에 담을 이야기를 골라 볼까요?"
    titles = "·".join(f"‘{s.title}’" for s in stories[:3])
    more = f" 그리고 {len(stories) - 3}편이 더 있어요." if len(stories) > 3 else ""
    return f"‘{title}’ 에는 {titles} 이야기가 담겼어요.{more} 생각이 어떻게 자랐는지 한 편씩 읽어 보세요."


def make_intro(scope: ProfileScope, title: str, stories: list[StoryRecord]) -> tuple[str, str]:
    """(머리말, 출처) — AI 를 못 쓰거나 실패하면 담긴 이야기 제목으로 만든 문장을 쓴다."""
    fallback = _fallback_intro(title, stories)
    if not ai_gate.allowed(scope.child) or not usage.try_consume(scope.child.id):
        return fallback, "fallback"
    try:
        out = ai_gate.call(
            purpose="books.intro",
            instructions=book_intro_instructions(),
            user_input=book_intro_input(title, [(s.title, s.summary) for s in stories]),
            schema=BookIntroLLM,
        )
    except ai_gate.LlmError:
        return fallback, "fallback"
    line = safe_ai_line(out.introduction, 600)
    return (line, "ai") if line else (fallback, "fallback")


# --- 직렬화 ---------------------------------------------------------------------


def _cover(book: StoryBook) -> BookCover | None:
    if not book.cover:
        return None
    return BookCover(theme=book.cover.get("theme"), emoji=book.cover.get("emoji"))


def summary_out(db: Session, book: StoryBook) -> BookSummary:
    return BookSummary(
        id=book.id,
        title=book.title,
        introduction=book.introduction,
        cover=_cover(book),
        status=book.status,  # type: ignore[arg-type]
        story_count=len(items(db, book.id)),
        version=book.version,
        created_at=iso(book.created_at) or "",
        updated_at=iso(book.updated_at) or "",
        completed_at=iso(book.completed_at),
    )


def stories_of(db: Session, book_id: str) -> list[StoryRecord]:
    rows = items(db, book_id)
    found = {s.id: s for s in db.scalars(select(StoryRecord).where(StoryRecord.id.in_([r.story_id for r in rows])))}
    return [found[r.story_id] for r in rows if r.story_id in found]


def detail_out(db: Session, book: StoryBook) -> BookDetail:
    return BookDetail(
        **summary_out(db, book).model_dump(),
        introduction_source=book.introduction_source,  # type: ignore[arg-type]
        stories=[story_summary(s) for s in stories_of(db, book.id)],
    )
