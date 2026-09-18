"""v1 이야기책 — 완성한 이야기를 골라 한 권으로 묶고, 순서를 바꾸고, 다 만들면 완성으로 표시한다.

책은 이야기를 가리키기만 한다. 책을 지워도 이야기는 책장에 그대로 남는다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import cursor, idempotency, library_books
from ..deps import ProfileScope, require_profile
from ..errors import ApiError
from ..library_common import check_version, child_text, current_user, own_story
from ..library_schemas import (
    BookCreateRequest,
    BookList,
    BookPatchRequest,
    BookResponse,
    BookStoryAddRequest,
)
from ..models_library import StoryBook, StoryBookItem

router = APIRouter(prefix="/books", tags=["v1-books"])


@router.get("", response_model=BookList)
def list_books(
    status: str | None = Query(default=None, pattern="^(DRAFT|COMPLETED)$"),
    cursor_raw: str | None = Query(default=None, alias="cursor"),
    limit: int | None = Query(default=None),
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    size = cursor.clamp_limit(limit)
    stmt = select(StoryBook).where(StoryBook.profile_id == scope.profile_id)
    if status:
        stmt = stmt.where(StoryBook.status == status)
    if cursor_raw:
        at, row_id = cursor.decode(cursor_raw)
        stmt = stmt.where(or_(StoryBook.updated_at < at, and_(StoryBook.updated_at == at, StoryBook.id < row_id)))
    rows = list(db.scalars(stmt.order_by(StoryBook.updated_at.desc(), StoryBook.id.desc()).limit(size + 1)))
    page = rows[:size]
    return BookList(
        items=[library_books.summary_out(db, b) for b in page],
        next_cursor=cursor.encode(page[-1].updated_at, page[-1].id) if len(rows) > size else None,
    )


@router.post("", response_model=BookResponse, status_code=201)
def create_book(
    req: BookCreateRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    idempotency_key: str | None = Header(default=None),
):
    """`generateIntroduction: true` 면 머리말을 AI 가 쓴다(막히면 담긴 이야기 제목으로 만든 문장)."""
    route = "POST /books"
    if (replayed := idempotency.replay(db, scope.user.id, idempotency_key, route)) is not None:
        return replayed
    if len(set(req.story_ids)) != len(req.story_ids):
        raise ApiError(400, "INVALID_INPUT", "같은 이야기를 두 번 담을 수 없어요.", {"fields": ["storyIds"]})
    stories = [own_story(db, scope.user.id, sid) for sid in req.story_ids]
    title = child_text(db, current_user(scope), req.title, 60)
    now = clock.now()
    introduction, source = ("", None)
    if req.generate_introduction:
        introduction, source = library_books.make_intro(scope, title, stories)
    book = StoryBook(
        profile_id=scope.profile_id,
        user_id=scope.user.id,
        title=title,
        introduction=introduction,
        introduction_source=source,
        cover=req.cover.model_dump(by_alias=True) if req.cover else None,
        created_at=now,
        updated_at=now,
    )
    db.add(book)
    db.flush()
    for position, story in enumerate(stories):
        db.add(StoryBookItem(book_id=book.id, story_id=story.id, position=position, created_at=now))
    db.flush()
    out = BookResponse(book=library_books.detail_out(db, book))
    idempotency.remember(db, scope.user.id, idempotency_key, route, out, status_code=201)
    db.commit()
    return out


@router.get("/{book_id}", response_model=BookResponse)
def get_book(book_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)):
    return BookResponse(book=library_books.detail_out(db, library_books.own_book(db, scope, book_id)))


@router.patch("/{book_id}", response_model=BookResponse)
def edit_book(
    book_id: str,
    req: BookPatchRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
    if_match: str | None = Header(default=None, alias="If-Match"),
):
    """제목·머리말·표지·이야기 순서를 고친다. 이야기를 더하고 빼는 것은 전용 엔드포인트로 한다."""
    book = library_books.own_book(db, scope, book_id)
    check_version(book.version, if_match, req.version)
    library_books.ensure_draft(book)
    if req.title is not None:
        book.title = child_text(db, current_user(scope), req.title, 60)
    if req.introduction is not None:
        book.introduction = child_text(db, current_user(scope), req.introduction, 600)
        book.introduction_source = None  # 아이가 직접 쓴 머리말이다
    if req.cover is not None:
        book.cover = req.cover.model_dump(by_alias=True)
    if req.story_ids is not None:
        library_books.reorder(db, book, req.story_ids)
    library_books.touch(book)
    db.commit()
    return BookResponse(book=library_books.detail_out(db, book))


@router.post("/{book_id}/stories", response_model=BookResponse, status_code=201)
def add_story(
    book_id: str,
    req: BookStoryAddRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    book = library_books.own_book(db, scope, book_id)
    library_books.ensure_draft(book)
    library_books.add_story(db, scope, book, req.story_id, req.position)
    library_books.touch(book)
    db.commit()
    return BookResponse(book=library_books.detail_out(db, book))


@router.delete("/{book_id}/stories/{story_id}", response_model=BookResponse)
def remove_story(
    book_id: str,
    story_id: str,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    """책에서만 빼낸다. 이야기 자체는 책장에 그대로 남는다."""
    book = library_books.own_book(db, scope, book_id)
    library_books.ensure_draft(book)
    library_books.remove_story(db, book, story_id)
    library_books.touch(book)
    db.commit()
    return BookResponse(book=library_books.detail_out(db, book))


@router.post("/{book_id}/complete", response_model=BookResponse)
def complete_book(
    book_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    """다 만들었다고 표시한다. 이미 완성한 책을 다시 불러도 같은 결과를 준다."""
    book = library_books.own_book(db, scope, book_id)
    if book.status != "COMPLETED":
        if not library_books.items(db, book.id):
            raise ApiError(409, "BOOK_EMPTY", "이야기를 한 편이라도 담아 볼까요?")
        book.status = "COMPLETED"
        book.completed_at = clock.now()
        library_books.touch(book)
        db.commit()
    return BookResponse(book=library_books.detail_out(db, book))


@router.delete("/{book_id}", status_code=204)
def delete_book(book_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)):
    """책만 지운다. 담겨 있던 이야기는 책장에 그대로 남는다."""
    book = library_books.own_book(db, scope, book_id)
    for row in library_books.items(db, book.id):
        db.delete(row)
    db.delete(book)
    db.commit()
    return Response(status_code=204)
