"""가족·아이·권한·기기 토큰·친구 모임.

보호자는 별도 문제 화면 대신 권한으로 관리한다(음성, 공유 둘러보기, 공유 요청).
아이는 보호자가 발급한 기기 토큰으로 태블릿에서 혼자 쓴다.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..auth import PERMISSION_KEYS, guardian_child, new_token, permission_enabled, require_child, require_guardian
from ..db import get_session
from ..models import Child, Circle, CircleMember, DeviceToken, Family, SafetyEvent
from ..schemas.family import (
    ChildCreateRequest,
    ChildOut,
    CircleCreateRequest,
    CircleJoinRequest,
    CircleOut,
    DeviceTokenResponse,
    FamilyCreateResponse,
    Permissions,
    SafetyEventOut,
)
from ..talks.onboarding import clean_nickname

router = APIRouter(tags=["family"])

DEV_NOTICE = "개발용 토큰이에요. 실제 보호자 본인 확인이나 법정대리인 동의 확인이 아니에요."


def child_out(child: Child) -> ChildOut:
    perms = {key: permission_enabled(child, key) for key in PERMISSION_KEYS}
    return ChildOut(
        id=child.id,
        nickname=child.nickname,
        grade=child.grade,
        affiliation=child.affiliation,  # type: ignore[arg-type]
        likes=list(child.likes or []),
        want_to_learn=list(child.want_to_learn or []),
        profile_confirmed=child.profile_confirmed,
        tester=child.is_tester,
        permissions=Permissions(**perms),
    )


@router.post("/families", response_model=FamilyCreateResponse, status_code=201)
def create_family(db: Session = Depends(get_session)) -> FamilyCreateResponse:
    raw, hashed = new_token("gt")
    family = Family(guardian_token_hash=hashed)
    db.add(family)
    db.commit()
    return FamilyCreateResponse(family_id=family.id, guardian_token=raw, notice=DEV_NOTICE)


@router.post("/guardian/children", response_model=ChildOut, status_code=201)
def create_child(
    req: ChildCreateRequest, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> ChildOut:
    child = Child(
        family_id=family.id,
        nickname=clean_nickname(req.nickname) or "",
        is_tester=req.tester,
        permissions={key: key == "voice" for key in PERMISSION_KEYS},
        onboarding={},
        likes=[],
        want_to_learn=[],
    )
    db.add(child)
    db.commit()
    return child_out(child)


@router.get("/guardian/children", response_model=list[ChildOut])
def list_children(family: Family = Depends(require_guardian), db: Session = Depends(get_session)) -> list[ChildOut]:
    children = db.scalars(select(Child).where(Child.family_id == family.id).order_by(Child.created_at))
    return [child_out(c) for c in children]


@router.get("/guardian/children/{child_id}", response_model=ChildOut)
def get_child(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> ChildOut:
    return child_out(guardian_child(child_id, family, db))


@router.put("/guardian/children/{child_id}/permissions", response_model=ChildOut)
def set_permissions(
    child_id: str,
    req: Permissions,
    family: Family = Depends(require_guardian),
    db: Session = Depends(get_session),
) -> ChildOut:
    child = guardian_child(child_id, family, db)
    child.permissions = {key: bool(getattr(req, key)) for key in PERMISSION_KEYS}
    db.commit()
    return child_out(child)


@router.post("/guardian/children/{child_id}/devices", response_model=DeviceTokenResponse, status_code=201)
def issue_device_token(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> DeviceTokenResponse:
    child = guardian_child(child_id, family, db)
    raw, hashed = new_token("ct")
    db.add(DeviceToken(child_id=child.id, token_hash=hashed))
    db.commit()
    return DeviceTokenResponse(child_id=child.id, child_token=raw, notice=DEV_NOTICE)


@router.delete("/guardian/children/{child_id}/devices", status_code=204)
def revoke_device_tokens(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> Response:
    child = guardian_child(child_id, family, db)
    db.execute(update(DeviceToken).where(DeviceToken.child_id == child.id).values(revoked=True))
    db.commit()
    return Response(status_code=204)


@router.get("/guardian/children/{child_id}/safety-events", response_model=list[SafetyEventOut])
def safety_events(
    child_id: str, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> list[SafetyEventOut]:
    child = guardian_child(child_id, family, db)
    events = db.scalars(
        select(SafetyEvent).where(SafetyEvent.child_id == child.id).order_by(SafetyEvent.created_at.desc())
    )
    return [
        SafetyEventOut(category=e.category, escalate=e.escalate, talk_id=e.talk_id, created_at=e.created_at)
        for e in events
    ]


@router.get("/me", response_model=ChildOut)
def me(child: Child = Depends(require_child)) -> ChildOut:
    return child_out(child)


# --- 친구 모임 --------------------------------------------------------------


def _circle_out(circle: Circle) -> CircleOut:
    return CircleOut(id=circle.id, name=circle.name, code=circle.code)


@router.post("/guardian/circles", response_model=CircleOut, status_code=201)
def create_circle(
    req: CircleCreateRequest, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> CircleOut:
    circle = Circle(name=req.name.strip(), code=secrets.token_hex(4).upper(), created_by=family.id)
    db.add(circle)
    db.flush()
    db.add(CircleMember(circle_id=circle.id, family_id=family.id))
    db.commit()
    return _circle_out(circle)


@router.post("/guardian/circles/join", response_model=CircleOut)
def join_circle(
    req: CircleJoinRequest, family: Family = Depends(require_guardian), db: Session = Depends(get_session)
) -> CircleOut:
    circle = db.scalar(select(Circle).where(Circle.code == req.code.strip().upper()))
    if circle is None:
        raise HTTPException(status_code=404, detail="circle_not_found")
    if db.get(CircleMember, (circle.id, family.id)) is None:
        db.add(CircleMember(circle_id=circle.id, family_id=family.id))
        db.commit()
    return _circle_out(circle)


@router.get("/guardian/circles", response_model=list[CircleOut])
def list_circles(family: Family = Depends(require_guardian), db: Session = Depends(get_session)) -> list[CircleOut]:
    circles = db.scalars(
        select(Circle)
        .join(CircleMember, CircleMember.circle_id == Circle.id)
        .where(CircleMember.family_id == family.id)
    )
    return [_circle_out(c) for c in circles]
