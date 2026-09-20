"""v1 법률 문서와 동의 — 문서 목록, 동의 기록 만들기·목록·철회.

- 문서는 코드에 둔 **법률 검토 전 초안**이다(`models_accounts.LEGAL_DOCUMENTS`). 개정일이 곧 버전이다.
- 동의의 `actor` 는 본문으로 받지 않는다. 로그인 계정과 연결 권한(MANAGE_DATA)에서 서버가 뽑는다.
- 문서 버전이 바뀌면 옛 동의는 `current: false` 가 되고 다시 받아야 한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import clock
from ...db import get_session
from .. import cursor, guests, idempotency, models_accounts
from ..cursor import iso
from ..deps import CurrentUser, require_user, resolve_scope
from ..errors import ApiError
from ..models_accounts import DOCUMENT_BY_ID, DRAFT_NOTICE, LEGAL_DOCUMENTS, Consent
from ..schemas_accounts import (
    ConsentActor,
    ConsentCreateRequest,
    ConsentListResponse,
    ConsentOut,
    ConsentResponse,
    ConsentWriteResponse,
    LegalDocumentListResponse,
    LegalDocumentOut,
)

router = APIRouter(tags=["v1-consents"])


def consent_out(consent: Consent) -> ConsentOut:
    document = DOCUMENT_BY_ID.get(consent.document_id)
    current = consent.revoked_at is None and document is not None and document.version == consent.document_version
    return ConsentOut(
        id=consent.id,
        profile_id=consent.profile_id,
        document_id=consent.document_id,
        document_version=consent.document_version,
        status="REVOKED" if consent.revoked_at else "GRANTED",
        current=current,
        actor=ConsentActor(user_id=consent.actor_user_id, role="GUARDIAN"),
        granted_at=iso(consent.granted_at) or "",
        revoked_at=iso(consent.revoked_at),
    )


@router.get("/legal-documents", response_model=LegalDocumentListResponse)
def legal_documents(
    locale: str = Query(default=models_accounts.DOCUMENT_LOCALE),
) -> LegalDocumentListResponse:
    if locale != models_accounts.DOCUMENT_LOCALE:
        message = "지금은 한국어 문서만 있어요."
        raise ApiError(400, "INVALID_INPUT", message, {"fields": ["locale"], "supported": ["ko-KR"]})
    items = [
        LegalDocumentOut(
            id=document.id,
            title=document.title,
            version=document.version,
            locale=locale,
            required=document.required,
            summary=document.summary,
            body=document.body,
            draft=True,
            draft_notice=DRAFT_NOTICE,
        )
        for document in LEGAL_DOCUMENTS
    ]
    return LegalDocumentListResponse(items=items, next_cursor=None)


@router.get("/consents", response_model=ConsentListResponse)
def list_consents(
    profile_id: str | None = Query(default=None, alias="profileId"),
    current_only: bool = Query(
        default=False, alias="currentOnly", description="현재 버전이며 철회되지 않은 동의만 페이지로 조회"
    ),
    limit: int | None = Query(default=None, ge=1, le=50),
    page_cursor: str | None = Query(default=None, alias="cursor"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ConsentListResponse:
    scope = resolve_scope(db, cu, profile_id, permission="VIEW_PROFILE")
    rows = list(
        db.scalars(
            select(Consent)
            .where(Consent.profile_id == scope.profile_id)
            .order_by(Consent.granted_at, Consent.id)
        )
    )
    if current_only:
        rows = [row for row in rows if consent_out(row).current]
    size = cursor.clamp_limit(limit)
    offset = cursor.decode_offset(page_cursor)
    has_more = offset + size < len(rows)
    return ConsentListResponse(
        items=[consent_out(row) for row in rows[offset : offset + size]],
        next_cursor=cursor.encode_offset(offset + size) if has_more else None,
    )


@router.post("/consents", response_model=ConsentWriteResponse, status_code=201)
def create_consents(
    req: ConsentCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
):
    """명세 26절 — 목적이 다른 동의를 버전별로 한 번에 기록한다. `agreed: false` 는 이미 한 동의를 철회한다."""
    if cu.user.role == "GUEST":
        if any(item.document_id not in guests.CONSENT_DOCUMENTS for item in req.items):
            raise ApiError(403, "GUEST_RESTRICTED", "게스트는 개인정보와 AI 대화 동의만 관리할 수 있어요.")
        if any(item.agreed for item in req.items) and not req.guardian_confirmed:
            raise ApiError(403, "GUARDIAN_CONFIRMATION_REQUIRED", "보호자가 직접 확인하고 동의해 주세요.")
        if any(item.agreed and not item.version for item in req.items):
            raise ApiError(400, "INVALID_INPUT", "확인한 동의서의 버전이 필요해요.")
    replay = idempotency.replay(db, cu.id, idempotency_key, "POST /consents")
    if replay is not None:
        return replay
    scope = resolve_scope(db, cu, req.profile_id, permission="MANAGE_DATA")
    now = clock.now()
    recorded: list[Consent] = []
    for item in req.items:
        document = DOCUMENT_BY_ID.get(item.document_id)
        if document is None:
            raise ApiError(404, "DOCUMENT_NOT_FOUND", "그런 문서가 없어요.", {"documentId": item.document_id})
        if item.version is not None and item.version != document.version:
            message = "동의서가 새로 바뀌었어요. 새 문서를 받아 주세요."
            details = {"documentId": document.id, "currentVersion": document.version}
            raise ApiError(409, "VERSION_CONFLICT", message, details)
        existing = models_accounts.active_consent(db, scope.profile_id, document.id)
        if not item.agreed:
            if existing is not None:
                existing.revoked_at = now
                recorded.append(existing)
            continue
        if existing is not None:
            recorded.append(existing)  # 같은 버전에 이미 동의했다. 새로 만들지 않는다.
            continue
        consent = Consent(
            profile_id=scope.profile_id,
            document_id=document.id,
            document_version=document.version,
            actor_user_id=cu.id,  # 본문의 actor 는 쓰지 않는다.
            actor_role="GUARDIAN",
            granted_at=now,
        )
        db.add(consent)
        db.flush()
        recorded.append(consent)
    body = ConsentWriteResponse(items=[consent_out(row) for row in recorded])
    idempotency.remember(db, cu.id, idempotency_key, "POST /consents", body, status_code=201)
    db.commit()
    return body


@router.delete("/consents/{consent_id}", response_model=ConsentResponse)
def revoke_consent(
    consent_id: str,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> ConsentResponse:
    consent = db.get(Consent, consent_id)
    if consent is None:
        raise ApiError(404, "CONSENT_NOT_FOUND", "동의 기록을 찾을 수 없어요.", {"consentId": consent_id})
    # 남의 프로필 동의는 있는지도 알리지 않는다.
    member = models_accounts.member_for(db, cu.user, consent.profile_id)
    if member is None:
        raise ApiError(404, "CONSENT_NOT_FOUND", "동의 기록을 찾을 수 없어요.", {"consentId": consent_id})
    if cu.user.role == "GUEST" and consent.document_id not in guests.CONSENT_DOCUMENTS:
        raise ApiError(403, "GUEST_RESTRICTED", "게스트는 개인정보와 AI 대화 동의만 관리할 수 있어요.")
    if "MANAGE_DATA" not in (member.permissions or []):
        message = "보호자 권한이 없어요."
        raise ApiError(403, "FORBIDDEN", message, {"profileId": consent.profile_id, "required": "MANAGE_DATA"})
    if consent.revoked_at is None:
        consent.revoked_at = clock.now()
        db.commit()
    return ConsentResponse(consent=consent_out(consent))
