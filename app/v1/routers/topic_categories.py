"""v1 주제 카테고리 — 기본 카테고리와 아이가 만든 카테고리.

기본 카테고리는 주제 은행 분류(`talks/topics.py`)를 v1 enum 으로 올린 코드 상수라 수정·삭제할 수 없다.
사용자 카테고리는 만든 아이 프로필에게만 보인다.
이름 안전 검사와 개수 제한은 기존 `routers/categories.py` 규칙을 따른다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from ...safety import topics as sensitive
from ...talks import topics as bank
from .. import topic_catalog
from ..activity_schemas import (
    TopicCategoryCreateRequest,
    TopicCategoryList,
    TopicCategoryOut,
    TopicCategoryResponse,
    TopicCategoryUpdateRequest,
)
from ..deps import ProfileScope, require_profile
from ..errors import ApiError
from ..models_activity import TopicCategoryRow

router = APIRouter(prefix="/topic-categories", tags=["v1-topic-categories"])

MAX_USER_CATEGORIES = 20
# 기본 카테고리 = 주제 은행 분류. id 는 주제 필터(`GET /topics?category=`)에 그대로 쓰는 값이다.
DEFAULT_IDS: tuple[str, ...] = ("SCIENCE", "MATH", "HISTORY", "THINKING", "DAILY_LIFE")
_VISUAL_OF_DEFAULT = {
    api: bank.CATEGORIES[key]["visual"] for key, api in topic_catalog.CATEGORY_OF_BANK.items() if api in DEFAULT_IDS
}
DEFAULT_NAMES = {topic_catalog.CATEGORY_NAMES[api] for api in DEFAULT_IDS}


def _default_items() -> list[TopicCategoryOut]:
    return [
        TopicCategoryOut(
            id=api,
            name=topic_catalog.CATEGORY_NAMES[api],
            kind="DEFAULT",
            order=index,
            visual=_VISUAL_OF_DEFAULT.get(api, "star"),
            editable=False,
        )
        for index, api in enumerate(DEFAULT_IDS)
    ]


def _user_item(row: TopicCategoryRow) -> TopicCategoryOut:
    return TopicCategoryOut(
        id=row.id, name=row.name, kind="USER", order=row.sort_order, visual="star", editable=True
    )


def _own(db: Session, scope: ProfileScope, category_id: str) -> TopicCategoryRow:
    if category_id in DEFAULT_IDS:
        message = "기본 카테고리는 바꾸거나 지울 수 없어요."
        raise ApiError(403, "CATEGORY_NOT_EDITABLE", message, {"categoryId": category_id})
    row = db.get(TopicCategoryRow, category_id)
    if row is None or row.profile_id != scope.profile_id:
        raise ApiError(404, "CATEGORY_NOT_FOUND", "카테고리를 찾을 수 없어요.", {"categoryId": category_id})
    return row


def _check_name(db: Session, scope: ProfileScope, name: str, exclude: str | None = None) -> str:
    name = " ".join(name.split())
    if not name or sensitive.detect(name):
        raise ApiError(422, "UNSAFE_CATEGORY", "그 이름으로는 카테고리를 만들 수 없어요. 다른 이름을 골라 볼까요?")
    if name in DEFAULT_NAMES:
        raise ApiError(409, "CATEGORY_EXISTS", "이미 있는 카테고리예요.", {"name": name})
    twin = db.scalar(
        select(TopicCategoryRow).where(TopicCategoryRow.profile_id == scope.profile_id, TopicCategoryRow.name == name)
    )
    if twin is not None and twin.id != exclude:
        raise ApiError(409, "CATEGORY_EXISTS", "이미 있는 카테고리예요.", {"name": name})
    return name


@router.get("", response_model=TopicCategoryList)
def list_topic_categories(scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)):
    mine = db.scalars(
        select(TopicCategoryRow)
        .where(TopicCategoryRow.profile_id == scope.profile_id)
        .order_by(TopicCategoryRow.sort_order, TopicCategoryRow.created_at)
    )
    return TopicCategoryList(items=[*_default_items(), *(_user_item(row) for row in mine)])


@router.post("", response_model=TopicCategoryResponse, status_code=201)
def create_topic_category(
    req: TopicCategoryCreateRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    name = _check_name(db, scope, req.name)
    count = (
        db.scalar(
            select(func.count())
            .select_from(TopicCategoryRow)
            .where(TopicCategoryRow.profile_id == scope.profile_id)
        )
        or 0
    )
    if count >= MAX_USER_CATEGORIES:
        raise ApiError(409, "TOO_MANY_CATEGORIES", "카테고리는 20개까지 만들 수 있어요.", {"max": MAX_USER_CATEGORIES})
    now = clock.now()
    row = TopicCategoryRow(
        user_id=scope.user.id,
        profile_id=scope.profile_id,
        name=name,
        sort_order=req.order if req.order is not None else count,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    return TopicCategoryResponse(category=_user_item(row))


@router.patch("/{category_id}", response_model=TopicCategoryResponse)
def update_topic_category(
    category_id: str,
    req: TopicCategoryUpdateRequest,
    scope: ProfileScope = Depends(require_profile),
    db: Session = Depends(get_session),
):
    row = _own(db, scope, category_id)
    if req.name is not None:
        row.name = _check_name(db, scope, req.name, exclude=row.id)
    if req.order is not None:
        row.sort_order = req.order
    row.updated_at = clock.now()
    db.commit()
    return TopicCategoryResponse(category=_user_item(row))


@router.delete("/{category_id}", status_code=204)
def delete_topic_category(
    category_id: str, scope: ProfileScope = Depends(require_profile), db: Session = Depends(get_session)
):
    db.delete(_own(db, scope, category_id))
    db.commit()
    return Response(status_code=204)
