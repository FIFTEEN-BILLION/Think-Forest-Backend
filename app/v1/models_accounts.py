"""v1 계정 테이블 — 프로필 소속, 프로필 설정, 보호자 초대, 동의 기록, 로그인 멱등.

계정 모델(사용자 결정): **로그인 계정 = 보호자·가족 계정**이다. 한 계정이 아이 프로필을 여러 개 가질 수 있고,
다른 계정도 초대로 같은 프로필에 붙는다. 누가 어떤 프로필에 무슨 권한으로 붙어 있는지는 `profile_members` 만 본다
(URL 의 profileId 를 믿지 않는다).

- 기존 테이블은 바꾸지 않는다(create_all 은 새 테이블만 만든다). `users.child_id` 는 그 계정의 **기본 프로필**로 남고,
  기존 계정은 처음 접근할 때 소속 행을 만들어 준다(`ensure_membership`).
- 프로필 하나마다 기존 엔진용 `children` 행이 하나 따로 있다(`child_profiles.child_id` 가 unique).
- 법률 문서는 코드에 둔 **법률 검토 전 초안**이다. 동의 기록은 문서 버전과 함께 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column, object_session

from ..db import Base
from ..models import Child
from .models import User, _now, prefixed_id
from .models_conversation import ChildProfile
from .permissions import ALL_PERMISSIONS

# ---------- 테이블 ----------


class ProfileMember(Base):
    """계정 ↔ 아이 프로필 연결. 보호자 연결(guardian-links)도 이 행 하나다."""

    __tablename__ = "profile_members"
    __table_args__ = (UniqueConstraint("user_id", "profile_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("lnk"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    child_id: Mapped[str] = mapped_column(ForeignKey("children.id"), index=True)
    role: Mapped[str] = mapped_column(String(12), default="OWNER")  # OWNER|GUARDIAN
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProfileSettings(Base):
    """프로필별 화면·보관 설정. 보관 기간을 줄이면 무엇이 언제 지워지는지 응답으로 알린다."""

    __tablename__ = "profile_settings"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("pst"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), unique=True, index=True)
    tts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    guardian_preview_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    theme: Mapped[str] = mapped_column(String(12), default="AUTO")  # AUTO|LIGHT|DARK(명세 17절)
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class GuardianInvitation(Base):
    """보호자 초대 — 1회용 짧은 토큰. 원문은 저장하지 않고 SHA-256 해시만 둔다."""

    __tablename__ = "guardian_invitations"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("ginv"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    inviter_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    permissions: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_user_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Consent(Base):
    """동의 기록. actor 는 본문이 아니라 로그인 계정에서 뽑는다. 철회해도 행은 남긴다(감사 기록)."""

    __tablename__ = "consents"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("cns"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("child_profiles.id"), index=True)
    document_id: Mapped[str] = mapped_column(String(40), index=True)
    document_version: Mapped[str] = mapped_column(String(20))
    actor_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    actor_role: Mapped[str] = mapped_column(String(12), default="GUARDIAN")
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class LoginIdempotency(Base):
    """로그인 재시도 대비 — 같은 `Idempotency-Key` 는 새 refresh 계열을 만들지 않고 쓰던 계열을 이어 쓴다.

    토큰 원문·응답 본문은 저장하지 않는다(계열 id 만 둔다).
    """

    __tablename__ = "login_idempotency"
    __table_args__ = (UniqueConstraint("user_id", "key"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: prefixed_id("lgi"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key: Mapped[str] = mapped_column(String(128))
    chain_id: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------- 법률 문서(초안) ----------

DOCUMENT_LOCALE = "ko-KR"
DRAFT_NOTICE = "법률 검토 전 초안입니다. 정식 고지·동의서는 검토 뒤에 바뀔 수 있어요."
AI_CONVERSATION_DOCUMENT = "ai_conversation"


@dataclass(frozen=True)
class LegalDocument:
    id: str
    title: str
    version: str  # 개정일(YYYY-MM-DD)
    required: bool
    summary: str
    body: str


LEGAL_DOCUMENTS: tuple[LegalDocument, ...] = (
    LegalDocument(
        id="privacy_child",
        title="아이 개인정보 수집·이용 동의(초안)",
        version="2026-09-18",
        required=True,
        summary="아이의 별명·학년대·관심사와 대화 기록을 서비스 제공에 쓰는 것에 동의합니다.",
        body=(
            "1. 수집 항목: 별명, 소속 종류(초등학교·홈스쿨 등), 학년 또는 나이대, 관심사, 티키와 나눈 대화 기록.\n"
            "2. 수집하지 않는 항목: 이름, 생년월일, 학교 이름, 주소, 전화번호, 사진·영상, 위치, 음성 원본.\n"
            "3. 이용 목적: 아이에게 맞춘 질문 제공, 이야기 정리본 생성, 보호자 리포트.\n"
            "4. 보관 기간: 프로필 설정의 보관 기간(기본 90일). 기간이 지난 대화 기록은 자동으로 지웁니다.\n"
            "5. 동의하지 않아도 서비스 체험은 할 수 있고, 언제든지 철회할 수 있습니다."
        ),
    ),
    LegalDocument(
        id=AI_CONVERSATION_DOCUMENT,
        title="AI 대화 처리 동의(초안)",
        version="2026-09-18",
        required=False,
        summary="아이가 쓴 문장을 AI 처리 사업자에게 보내 티키의 답을 만드는 것에 동의합니다.",
        body=(
            "1. 처리 내용: 아이가 쓴 문장에서 개인정보를 가린 뒤 AI 처리 사업자에게 보내 티키의 다음 질문을 만듭니다.\n"
            "2. 보내지 않는 것: 이름, 학교 이름, 주소, 전화번호, 음성 원본.\n"
            "3. 사업자는 받은 문장을 모델 학습에 쓰지 않습니다.\n"
            "4. 동의하지 않으면 AI 대신 규칙 기반 대사로 대화를 이어 갑니다. 대화 자체는 막지 않습니다.\n"
            "5. 언제든지 철회할 수 있고, 철회하면 바로 규칙 기반 대화로 돌아갑니다."
        ),
    ),
    LegalDocument(
        id="voice_retention",
        title="음성 인식·보관 동의(초안)",
        version="2026-09-18",
        required=False,
        summary="말로 답하기를 쓸 때 음성을 글로 바꾸는 처리에 동의합니다.",
        body=(
            "1. 처리 내용: 아이가 말한 음성을 글로 바꾸어 대화에 씁니다.\n"
            "2. 음성 원본은 저장하지 않고, 글로 바꾼 뒤 바로 버립니다.\n"
            "3. 글로 바꾼 문장은 대화 기록과 같은 보관 기간을 따릅니다.\n"
            "4. 동의하지 않으면 글로 쓰기만 쓸 수 있습니다."
        ),
    ),
    LegalDocument(
        id="community_share",
        title="이야기 공유 동의(초안)",
        version="2026-09-18",
        required=False,
        summary="아이가 완성한 이야기를 별명과 함께 다른 가족에게 보여 주는 것에 동의합니다.",
        body=(
            "1. 공개 범위: 아이가 고른 이야기 한 편과 별명. 보호자가 먼저 보고 승인한 것만 올라갑니다.\n"
            "2. 공개하지 않는 것: 실명, 학교 이름, 대화 원문 전체.\n"
            "3. 언제든지 내릴 수 있고, 내리면 다른 가족에게 더는 보이지 않습니다.\n"
            "4. 동의하지 않아도 아이의 책장은 그대로 쓸 수 있습니다."
        ),
    ),
)

DOCUMENT_BY_ID: dict[str, LegalDocument] = {doc.id: doc for doc in LEGAL_DOCUMENTS}


# ---------- 소속·동의 조회 ----------


def owner_permissions() -> list[str]:
    return list(ALL_PERMISSIONS)


def prepare_onboarding_profile(db: Session, user: User) -> None:
    """첫인사 전에도 동의할 대상이 필요하다. 완료 표시 없이 빈 프로필만 준비한다."""
    if db.scalar(select(ChildProfile.id).where(ChildProfile.child_id == user.child_id)) is not None:
        return
    try:
        with db.begin_nested():
            db.add(ChildProfile(child_id=user.child_id, user_id=user.id))
            db.flush()
    except IntegrityError:
        # 동시에 열린 탭에서 같은 아이의 프로필을 준비했다면 그 행을 사용한다.
        if db.scalar(select(ChildProfile.id).where(ChildProfile.child_id == user.child_id)) is None:
            raise


def ensure_membership(db: Session, user: User) -> None:
    """기존 계정 이어받기 — 소속 행이 없으면 `users.child_id` 의 프로필을 기본 프로필로 만들어 준다.

    ALTER 없이 런타임에 채운다. 프로필이 아직 없으면(첫인사 전) 할 일이 없다.
    """
    existing = db.scalar(select(ProfileMember.id).where(ProfileMember.user_id == user.id).limit(1))
    if existing is not None:
        return
    profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == user.child_id))
    if profile is None:
        return
    member = ProfileMember(
        user_id=user.id,
        profile_id=profile.id,
        child_id=profile.child_id,
        role="OWNER",
        permissions=owner_permissions(),
        is_default=True,
    )
    try:
        with db.begin_nested():
            db.add(member)
            db.flush()
    except IntegrityError:
        if db.scalar(select(ProfileMember.id).where(
            ProfileMember.user_id == user.id, ProfileMember.profile_id == profile.id,
        )) is None:
            raise


def active_members(db: Session, user: User) -> list[ProfileMember]:
    ensure_membership(db, user)
    rows = db.scalars(
        select(ProfileMember)
        .where(ProfileMember.user_id == user.id, ProfileMember.revoked_at.is_(None))
        .order_by(ProfileMember.is_default.desc(), ProfileMember.created_at, ProfileMember.id)
    )
    return list(rows)


def member_for(db: Session, user: User, profile_id: str) -> ProfileMember | None:
    ensure_membership(db, user)
    return db.scalar(
        select(ProfileMember).where(
            ProfileMember.user_id == user.id,
            ProfileMember.profile_id == profile_id,
            ProfileMember.revoked_at.is_(None),
        )
    )


def default_member(db: Session, user: User) -> ProfileMember | None:
    members = active_members(db, user)
    return members[0] if members else None


def active_consent(db: Session, profile_id: str, document_id: str) -> Consent | None:
    """현재 문서 버전과 같고 철회되지 않은 동의."""
    document = DOCUMENT_BY_ID.get(document_id)
    if document is None:
        return None
    return db.scalar(
        select(Consent)
        .where(
            Consent.profile_id == profile_id,
            Consent.document_id == document_id,
            Consent.document_version == document.version,
            Consent.revoked_at.is_(None),
        )
        .order_by(Consent.granted_at.desc())
    )


def missing_consents(db: Session, profile_id: str, document_ids: tuple[str, ...]) -> list[str]:
    return [doc_id for doc_id in document_ids if active_consent(db, profile_id, doc_id) is None]


def child_has_ai_consent(child: Child) -> bool:
    """AI 관문용 — 이 아이 프로필에 보호자가 남긴 현재 버전의 AI 대화 동의가 있는지.

    호출하는 쪽(ai_gate)은 DB 세션을 인자로 받지 않으므로 Child 인스턴스가 붙어 있는 세션을 그대로 쓴다.
    새 세션을 열면 안 된다 — 메모리 SQLite(StaticPool)에서는 연결을 공유해서, 닫는 순간 바깥 트랜잭션이 되돌아간다.
    """
    db = object_session(child)
    if db is None:
        return False
    with db.no_autoflush:
        profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == child.id))
        if profile is None:
            return False
        consent = active_consent(db, profile.id, AI_CONVERSATION_DOCUMENT)
    return consent is not None and consent.actor_role == "GUARDIAN"


def guest_needs_consent(child: Child) -> bool:
    """게스트는 데이터 모드와 무관하게 두 동의가 현재 유효해야 실제 AI를 쓴다."""
    from .guests import CONSENT_DOCUMENTS

    db = object_session(child)
    if db is None:
        return False
    with db.no_autoflush:
        user = db.scalar(select(User).where(User.child_id == child.id, User.role == "GUEST"))
        if user is None:
            return False
        profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == child.id))
        if profile is None:
            return True
        for document_id in CONSENT_DOCUMENTS:
            consent = active_consent(db, profile.id, document_id)
            if consent is None or consent.actor_role != "GUARDIAN":
                return True
    return False


def retention_default() -> int:
    from ..config import get_settings

    return get_settings().data_retention_days
