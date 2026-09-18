"""로컬 thinkforest.db에 친구 이야기 예시 3개를 추가한다.

실행: python -m app.seed_community (backend 폴더)
운영 DATABASE_URL은 사용하지 않는다. 기존 데이터와 이미 추가한 예시는 변경하지 않는다.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from . import clock
from .models import Child, Family
from .v1 import tables  # noqa: F401 — 전체 외래 키 테이블 등록
from .v1.models import User
from .v1.models_accounts import ProfileMember
from .v1.models_conversation import ChildProfile, ConversationOwner, ConversationSession, StoryRecord
from .v1.models_social import PublicStory, ShareRequest
from .v1.permissions import ALL_PERMISSIONS

EXAMPLES = [
    {
        "key": "cloud",
        "author": "구름이(예시)",
        "category": "SCIENCE",
        "title": "얼음물 컵에 맺힌 작은 물방울",
        "excerpt": "컵이 새는 줄 알았는데, 공기 속에도 물이 숨어 있었어요.",
        "body": (
            "얼음물을 담은 컵 바깥에 작은 물방울이 생겼다. 처음에는 컵에 아주 작은 구멍이 있는 줄 알았다.\n\n"
            "그래서 컵 바깥을 닦고 다시 기다려 보았다. 물방울은 또 생겼다. "
            "옆에 둔 미지근한 물컵에는 물방울이 거의 생기지 않았다.\n\n"
            "공기 속 수증기가 차가운 컵을 만나 물방울이 된다는 것을 알게 됐다. "
            "눈에 안 보인다고 아무것도 없는 건 아니었다. 다음에는 거울에 입김을 불어 보고 싶다."
        ),
        "initial": "컵에 작은 구멍이 나서 물이 새는 걸까?",
        "evidence": ["컵을 닦아도 물방울이 다시 생겼다.", "미지근한 물컵에는 물방울이 거의 없었다."],
        "reflection": "보이는 모습만으로 정하지 않고, 조건을 바꾸어 비교해 보고 싶다.",
    },
    {
        "key": "star",
        "author": "별콩이(예시)",
        "category": "IMAGINATION",
        "title": "달에 문을 연 작은 도서관",
        "excerpt": "달나라 도서관에서는 책 한 권을 빌릴 때 궁금한 질문 하나를 남겨요.",
        "body": (
            "달에 도서관을 만든다면 책이 둥둥 떠다닐 것 같다. "
            "나는 책마다 긴 리본을 달아서 책장에 살짝 묶어 두기로 했다.\n\n"
            "도서관에 온 토끼는 지구의 바다가 왜 파란지 궁금해했다. "
            "우리는 창가에 앉아 지구를 바라보고, 함께 찾아볼 질문을 쪽지에 적었다.\n\n"
            "이 도서관에서는 책을 빌릴 때 돈 대신 질문 하나를 남긴다. "
            "다음에 온 친구가 그 질문에 자기 생각을 덧붙인다. 서로 다른 생각이 모이면 새 책이 된다."
        ),
        "initial": "달에도 친구들이 모여 책을 읽는 곳이 있으면 좋겠다.",
        "evidence": ["떠다니는 책을 잡을 수 있도록 리본을 달았다.", "친구의 질문에 내 생각을 보탰다."],
        "reflection": "정답을 바로 몰라도 함께 질문하면 재미있는 이야기를 만들 수 있다.",
    },
    {
        "key": "leaf",
        "author": "새싹이(예시)",
        "category": "FEELINGS",
        "title": "천천히 걸어도 함께 가는 길",
        "excerpt": "빨리 도착하는 것보다 친구와 같이 발견하는 즐거움이 더 컸어요.",
        "body": (
            "친구와 산책하는데 친구가 자꾸 멈췄다. 나는 빨리 놀이터에 가고 싶어서 조금 답답했다.\n\n"
            "왜 멈추는지 물어보니 친구는 길가의 작은 꽃을 보고 있었다. "
            "나도 옆에 앉아 보니 꽃잎 색이 조금씩 달랐고, 잎 위에는 작은 물방울이 있었다.\n\n"
            "우리는 놀이터까지 천천히 걸으며 새로 발견한 것을 하나씩 말하기로 했다. "
            "친구가 느린 게 아니라 내가 못 보던 것을 보고 있었던 것 같다. "
            "다음에는 재촉하기 전에 친구의 이야기를 먼저 들어야겠다."
        ),
        "initial": "친구가 자꾸 멈춰서 답답했다.",
        "evidence": ["멈춘 이유를 물으니 작은 꽃을 보고 있다고 했다.", "함께 보니 나도 새로운 것을 발견했다."],
        "reflection": "서로 속도가 달라도 이유를 듣고 함께 가는 방법을 찾을 수 있다.",
    },
]


def seed(db: Session) -> list[str]:
    added = []
    now = clock.now()
    for index, example in enumerate(EXAMPLES):
        key = example["key"]
        public_id = f"pub_example_{key}"
        if db.get(PublicStory, public_id) is not None:
            continue
        family_id, child_id = f"fam_example_{key}", f"chd_example_{key}"
        user_id, profile_id = f"usr_example_{key}", f"prf_example_{key}"
        session_id, story_id, share_id = f"cnv_example_{key}", f"sty_example_{key}", f"shr_example_{key}"
        # 토큰·외부 로그인 연결 없이 예시 작성자만 만든다.
        db.add(Family(id=family_id, guardian_token_hash=secrets.token_hex(32)))
        db.flush()
        db.add(Child(id=child_id, family_id=family_id, nickname=example["author"], profile_confirmed=True))
        db.flush()
        db.add(User(id=user_id, family_id=family_id, child_id=child_id))
        db.flush()
        db.add(ChildProfile(
            id=profile_id, child_id=child_id, user_id=user_id, nickname=example["author"],
            grade_or_age_band="초등 저학년", completed_at=now,
        ))
        db.flush()
        db.add(ProfileMember(
            user_id=user_id, profile_id=profile_id, child_id=child_id,
            role="OWNER", permissions=list(ALL_PERMISSIONS), is_default=True,
        ))
        db.add(ConversationSession(
            id=session_id, user_id=user_id, kind="STORY", status="COMPLETED",
            topic={"title": example["title"], "category": example["category"]},
            story_id=story_id, completed_at=now,
        ))
        db.flush()
        journey = {
            "initialIdea": example["initial"], "evidence": example["evidence"],
            "finalReflection": example["reflection"],
        }
        db.add(ConversationOwner(session_id=session_id, child_id=child_id))
        db.add(StoryRecord(
            id=story_id, user_id=user_id, session_id=session_id, category=example["category"],
            topic_title=example["title"], title=example["title"], summary=example["excerpt"],
            body=example["body"], thought_journey=journey,
        ))
        db.flush()
        db.add(ShareRequest(
            id=share_id, user_id=user_id, story_id=story_id, audience="PEERS", hide_profile=False,
            status="PUBLISHED", confirmed_body_version=1, confirmed_redactions=True,
            public_story_id=public_id, decided_at=now,
        ))
        db.add(PublicStory(
            id=public_id, share_request_id=share_id, story_id=story_id, author_user_id=user_id,
            display_name=example["author"], age_band="초등 저학년", category=example["category"],
            title=example["title"], excerpt=example["excerpt"], body=example["body"],
            thought_journey=journey, audience="PEERS", status="PUBLISHED",
            published_at=now - timedelta(minutes=index),
        ))
        db.flush()
        added.append(public_id)
    return added


def main() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "thinkforest.db"
    if not path.is_file():
        raise SystemExit("먼저 로컬 서버를 실행해서 data/thinkforest.db를 만들어 주세요.")
    backup = path.parent / "backups" / f"thinkforest.before-community-{clock.now():%Y%m%dT%H%M%S%f}.db"
    backup.parent.mkdir(exist_ok=True)
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    engine = create_engine(URL.create("sqlite", database=str(path)))

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        with Session(engine) as db, db.begin():
            added = seed(db)
            rows = db.scalars(select(PublicStory).where(PublicStory.id.in_(
                [f"pub_example_{item['key']}" for item in EXAMPLES]
            ))).all()
            assert len(rows) == 3
        print(f"Added {len(added)} example stories to {path}")
        print(f"Backup: {backup}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
