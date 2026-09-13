"""아이가 '!' 버튼으로 직접 만드는 카테고리와 그 카테고리의 대화 주제 제안."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import ai_block_reason, require_child
from ..db import get_session
from ..models import Category, Child
from ..prompts import talk as prompt
from ..safety import topics as sensitive
from ..schemas.talk import CategoryIn, CategoryOut, TopicOut, TopicSuggestions, TopicSuggestLLM
from ..services import usage
from ..services.llm import LlmError, call_structured
from ..talks import topics as bank
from ..talks.planner import clip

router = APIRouter(prefix="/children/me/categories", tags=["categories"])

MAX_CUSTOM_CATEGORIES = 20


def category_list(db: Session, child: Child) -> list[CategoryOut]:
    system = [
        CategoryOut(id=key, name=value["name"], kind="system", visual=value["visual"])
        for key, value in bank.CATEGORIES.items()
        if key != "custom"
    ]
    custom = [
        CategoryOut(id=c.id, name=c.name, kind="custom", visual="star")
        for c in db.scalars(select(Category).where(Category.child_id == child.id).order_by(Category.created_at))
    ]
    return system + custom


def _own_category(db: Session, child: Child, category_id: str) -> Category:
    category = db.get(Category, category_id)
    if category is None or category.child_id != child.id:
        raise HTTPException(status_code=404, detail="category_not_found")
    return category


def fallback_topics(name: str) -> list[TopicOut]:
    hooks = (
        ("궁금한 점", f"'{name}' 하면 떠오르는 궁금한 점은 뭐야?"),
        ("없는 하루", f"'{name}' 없는 하루를 상상해 볼까? 무엇이 달라질까?"),
        ("친구에게 알려 주기", f"'{name}'에 대해 친구에게 꼭 알려 주고 싶은 것은 뭐야?"),
    )
    return [TopicOut(id=None, category="custom", title=t, hook=h, visual="star", source="fallback") for t, h in hooks]


@router.get("", response_model=list[CategoryOut])
def list_categories(child: Child = Depends(require_child), db: Session = Depends(get_session)) -> list[CategoryOut]:
    return category_list(db, child)


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(
    req: CategoryIn, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> CategoryOut:
    name = " ".join(req.name.split())
    if not name or sensitive.detect(name):
        raise HTTPException(status_code=422, detail="unsafe_category")
    count = db.scalar(select(func.count()).select_from(Category).where(Category.child_id == child.id)) or 0
    if count >= MAX_CUSTOM_CATEGORIES:
        raise HTTPException(status_code=409, detail="too_many_categories")
    if name in (v["name"] for v in bank.CATEGORIES.values()) or db.scalar(
        select(Category).where(Category.child_id == child.id, Category.name == name)
    ):
        raise HTTPException(status_code=409, detail="category_exists")
    category = Category(child_id=child.id, name=name)
    db.add(category)
    db.commit()
    return CategoryOut(id=category.id, name=category.name, kind="custom", visual="star")


@router.delete("/{category_id}", status_code=204)
def delete_category(
    category_id: str, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> Response:
    db.delete(_own_category(db, child, category_id))
    db.commit()
    return Response(status_code=204)


@router.post("/{category_id}/topics", response_model=TopicSuggestions)
def suggest_topics(
    category_id: str, child: Child = Depends(require_child), db: Session = Depends(get_session)
) -> TopicSuggestions:
    category = _own_category(db, child, category_id)
    error = ai_block_reason(child) or (None if usage.try_consume(child.id) else "daily_limit")
    if error:
        return TopicSuggestions(ai=False, error=error, topics=fallback_topics(category.name))
    profile = {"nickname": child.nickname, "grade": child.grade, "likes": child.likes}
    try:
        out = call_structured(
            purpose="categories.topics",
            instructions=prompt.topics_instructions(),
            user_input=prompt.topics_input(category.name, profile),
            schema=TopicSuggestLLM,
        )
    except LlmError as exc:
        return TopicSuggestions(ai=False, error=f"ai_failed:{exc.code}", topics=fallback_topics(category.name))
    topics = []
    for idea in out.topics[:3]:
        title, hook = clip(idea.title, 20), clip(idea.hook, 100)
        if len(hook) >= 5 and title and not sensitive.detect(title) and not sensitive.detect(hook):
            topics.append(TopicOut(id=None, category="custom", title=title, hook=hook, visual=idea.visual, source="ai"))
    if not topics:
        return TopicSuggestions(ai=False, error="output_replaced", topics=fallback_topics(category.name))
    return TopicSuggestions(ai=True, topics=topics)
