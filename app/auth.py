"""토큰 인증과 권한 — 개발용.

실제 보호자 본인 확인이나 법정대리인 동의 확인이 아니다. 운영 전 Supabase Auth 로 교체한다.
- 보호자 토큰(gt_): 가족 단위. 아이 생성·권한 부여·공유 승인·단어 검사·상담.
- 아이 기기 토큰(ct_): 보호자가 발급한 태블릿용. 아이는 이 토큰으로 혼자 쓴다.
토큰 원문은 저장하지 않고 SHA-256 해시만 둔다.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session
from .models import Child, DeviceToken, Family

PERMISSION_KEYS: tuple[str, ...] = ("voice", "browse_shared", "publish_request")


def new_token(prefix: str) -> tuple[str, str]:
    raw = f"{prefix}_{secrets.token_urlsafe(32)}"
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_token")
    return authorization.removeprefix("Bearer ").strip()


def _family(raw: str, db: Session) -> Family | None:
    if not raw.startswith("gt_"):
        return None
    return db.scalar(select(Family).where(Family.guardian_token_hash == hash_token(raw)))


def _child(raw: str, db: Session) -> Child | None:
    if not raw.startswith("ct_"):
        return None
    token = db.scalar(
        select(DeviceToken).where(DeviceToken.token_hash == hash_token(raw), DeviceToken.revoked.is_(False))
    )
    return db.get(Child, token.child_id) if token else None


def require_guardian(
    authorization: str | None = Header(default=None), db: Session = Depends(get_session)
) -> Family:
    family = _family(_bearer(authorization), db)
    if family is None:
        raise HTTPException(status_code=401, detail="invalid_guardian_token")
    return family


def require_child(authorization: str | None = Header(default=None), db: Session = Depends(get_session)) -> Child:
    child = _child(_bearer(authorization), db)
    if child is None:
        raise HTTPException(status_code=401, detail="invalid_child_token")
    return child


@dataclass(frozen=True)
class Viewer:
    family_id: str
    child: Child | None


def require_viewer(authorization: str | None = Header(default=None), db: Session = Depends(get_session)) -> Viewer:
    """보호자 또는 아이. 공유 목록처럼 둘 다 볼 수 있는 곳에 쓴다."""
    raw = _bearer(authorization)
    family = _family(raw, db)
    if family:
        return Viewer(family.id, None)
    child = _child(raw, db)
    if child:
        return Viewer(child.family_id, child)
    raise HTTPException(status_code=401, detail="invalid_token")


def guardian_child(child_id: str, family: Family, db: Session) -> Child:
    child = db.get(Child, child_id)
    if child is None or child.family_id != family.id:
        raise HTTPException(status_code=404, detail="child_not_found")
    return child


def require_permission(child: Child, key: str) -> None:
    if not (child.permissions or {}).get(key):
        raise HTTPException(status_code=403, detail=f"permission_required:{key}")


def ai_block_reason(child: Child) -> str | None:
    """이 아이의 문장·음성을 OpenAI 로 보내도 되는지. 막히면 사유 코드.

    OpenAI 18세 미만 지침: 13세 미만 개인정보는 ZDR 승인 뒤에만 처리한다.
    ZDR 승인 전(CHILD_DATA_MODE=demo)에는 성인 테스터 계정만 실제 AI 를 쓴다.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        return "no_api_key"
    if not settings.ai_switch:
        return "ai_disabled"
    if settings.child_data_mode != "child" and not child.is_tester:
        return "child_data_mode_off"
    return None
