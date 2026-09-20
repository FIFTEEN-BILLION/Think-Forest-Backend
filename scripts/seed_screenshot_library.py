"""명시한 로컬 프로필에만 스크린샷용 합성 이야기·단어·책을 추가한다.

python -m scripts.seed_screenshot_library --profile-id <id> [--apply]
기본은 미리보기. 기존 행을 수정/삭제하거나 공개 게시하지 않는다.
추가 행의 ID는 *_shot_*이고 대화 readiness/meta에 screenshotSeed 표시를 남긴다.
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from app import clock, db
from app.v1 import conversation_scope, tables  # noqa: F401 — 모델 등록
from app.v1.models import User
from app.v1.models_conversation import ChildProfile, ConversationMessage, ConversationSession, StoryRecord
from app.v1.models_library import StoryBook, StoryBookItem, WordbookEntry
from app.v1.story_engine import story_out
from sqlalchemy import select

SEED = "screenshot-library-v1"
STORIES = [
    (
        "SCIENCE",
        "구름은 어떻게 하늘에 떠 있을까?",
        "보이지 않는 물이 하늘로 여행하는 모습을 상상했어요.",
        "처음에는 구름이 솜처럼 가벼워서 하늘에 떠 있다고 생각했다.",
        "햇빛을 받은 물은 수증기가 되어 올라간다. 높은 곳에서 식으면 아주 작은 물방울이 된다.",
        "구름은 솜이 아니라 작은 물방울과 얼음 알갱이가 모인 것이었다. "
        "다음에는 구름의 모양이 바뀌는 것도 관찰해 보고 싶다.",
    ),
    (
        "NATURE",
        "작은 씨앗의 커다란 여행",
        "씨앗 하나가 나무가 되기까지 필요한 것을 찾아봤어요.",
        "씨앗을 땅에 심으면 저절로 나무가 된다고 생각했다.",
        "씨앗이 싹을 틔우려면 물과 알맞은 온도, 공기가 필요하다. 싹이 자란 뒤에는 햇빛도 중요하다.",
        "내일은 화분의 흙을 만져 보고 물이 필요한지 살펴볼 것이다. 작은 변화도 기록하면 식물의 성장을 알아볼 수 있다.",
    ),
    (
        "IMAGINATION",
        "달에 놀이터를 만든다면",
        "둥실 떠오르는 미끄럼틀과 우주 그네를 상상했어요.",
        "달에 가면 아주 높은 미끄럼틀을 타 보고 싶다.",
        "달의 중력은 지구보다 약하다. 뛰면 더 높이 올라갈 수 있지만 안전하게 내려오는 방법도 필요하다.",
        "손잡이와 안전줄이 있는 달 놀이터를 그려 보았다. 재미있는 상상에 안전한 방법을 더하니 더 멋진 놀이터가 되었다.",
    ),
    (
        "THINKING",
        "틀려도 괜찮은 발명가",
        "실수를 새로운 방법을 찾는 기회로 바라보았어요.",
        "종이 다리가 무너지면 실험에 실패한 것이라고 생각했다.",
        "종이를 여러 번 접고 다리의 기둥 간격도 바꾸어 보았다. 전보다 더 많은 지우개를 올릴 수 있었다.",
        "틀린 방법도 무엇을 바꿔야 하는지 알려 준다. 다음에는 왜 무너졌는지 먼저 질문하고 새로운 방법으로 도전하겠다.",
    ),
    (
        "FEELINGS",
        "마음에도 날씨가 있을까?",
        "내 마음을 날씨에 빗대어 표현하는 연습을 했어요.",
        "친구와 다투면 내 마음은 비 오는 날처럼 느껴진다.",
        "속상한 마음을 그림으로 그리고 왜 그랬는지 이야기해 보니 조금 편안해졌다.",
        "마음의 날씨는 계속 바뀔 수 있다. 친구의 마음에도 어떤 날씨가 있는지 물어보고 서로의 이야기를 들어 주고 싶다.",
    ),
    (
        "DAILY_LIFE",
        "우리 집의 작은 지구 지키기",
        "매일 할 수 있는 작은 행동을 찾아 약속했어요.",
        "지구를 지키는 일은 어른들만 할 수 있는 큰일이라고 생각했다.",
        "양치할 때 컵을 쓰고, 쓰지 않는 불을 끄고, 종이를 나누어 버리는 것도 도움이 된다.",
        "나는 먼저 양치컵 쓰기를 실천해 보기로 했다. "
        "작은 약속을 매일 지키면 습관이 되고, 함께하면 더 큰 변화를 만들 수 있다.",
    ),
    (
        "MATH",
        "피자를 공평하게 나누는 방법",
        "같은 크기로 나누는 방법을 그림으로 찾아봤어요.",
        "피자 조각의 개수만 같으면 공평하다고 생각했다.",
        "큰 조각 두 개와 작은 조각 두 개는 양이 달랐다. 가운데를 지나도록 자르면 같은 크기로 나누기 쉬웠다.",
        "개수뿐 아니라 크기도 살펴봐야 공평하게 나눌 수 있다. 다음에는 네 친구가 나누는 방법도 그려 보고 싶다.",
    ),
]
WORDS = [
    (
        "관찰",
        "어떤 모습을 자세히 살펴보는 것",
        "나는 구름의 모양을 관찰했어.",
        "오늘은 나뭇잎을 관찰했다.",
        "FAMILIAR",
        0,
    ),
    ("수증기", "물이 기체 상태로 바뀐 것", "눈에 보이지 않는 수증기가 공기 중에 있어.", None, "NEW", 0),
    (
        "성장",
        "몸이나 크기, 생각이 점점 자라는 것",
        "씨앗이 작은 나무로 성장했어.",
        "매일 책을 읽으며 생각이 성장해요.",
        "PRACTICING",
        1,
    ),
    (
        "새싹",
        "씨앗에서 처음 돋아나는 어린 싹",
        "화분에서 초록색 새싹이 나왔어.",
        "작은 새싹에게 물을 주었어요.",
        "FAMILIAR",
        1,
    ),
    ("중력", "물체가 서로 끌어당기는 힘", "지구의 중력 때문에 공이 아래로 떨어져.", None, "NEW", 2),
    (
        "상상",
        "눈앞에 없는 모습을 마음속으로 그려 보는 것",
        "달에 있는 놀이터를 상상해 보았어.",
        "나는 하늘을 나는 자전거를 상상했어요.",
        "PRACTICING",
        2,
    ),
    ("도전", "어렵거나 새로운 일을 해 보려고 나서는 것", "더 튼튼한 종이 다리 만들기에 도전했어.", None, "NEW", 3),
    (
        "공감",
        "다른 사람의 생각이나 마음을 함께 느끼고 이해하는 것",
        "친구의 속상한 마음에 공감했어.",
        "친구 이야기를 끝까지 듣고 공감했어요.",
        "FAMILIAR",
        4,
    ),
    (
        "습관",
        "여러 번 되풀이해서 자연스럽게 하게 된 행동",
        "양치할 때 컵을 쓰는 습관이 생겼어.",
        "자기 전에 책을 읽는 습관을 만들고 싶어요.",
        "PRACTICING",
        5,
    ),
]


def row_id(profile_id: str, prefix: str, key: str) -> str:
    return f"{prefix}_shot_{uuid5(NAMESPACE_URL, f'{SEED}/{profile_id}/{prefix}/{key}').hex}"


def seed_library(session, profile_id: str) -> dict:
    profile = session.get(ChildProfile, profile_id)
    user = session.get(User, profile.user_id) if profile else None
    if profile is None or user is None or user.status != "ACTIVE":
        raise ValueError("활성 프로필을 찾을 수 없습니다")
    now = clock.now()
    created = {"stories": [], "words": [], "books": []}
    for index, (category, title, summary, initial, evidence, reflection) in enumerate(STORIES):
        story_id = row_id(profile_id, "sty", str(index))
        if session.get(StoryRecord, story_id):
            continue
        at = now - timedelta(days=index)
        sid = row_id(profile_id, "cnv", str(index))
        if session.get(ConversationSession, sid):
            raise ValueError("예시 대화 ID가 이미 있어 덮어쓰지 않았습니다")
        conversation = ConversationSession(
            id=sid,
            user_id=user.id,
            kind="STORY",
            status="COMPLETED",
            topic={"apiId": None, "title": title, "apiCategory": category},
            readiness={
                "screenshotSeed": SEED,
                "covered": ["EXPERIENCE", "IDEA", "REASON", "ALTERNATIVE", "REFLECTION"],
                "validResponses": 3,
                "extraTurns": 0,
                "readyAnnounced": True,
            },
            story_id=story_id,
            active_seconds=0,
            last_message_at=at,
            completed_at=at,
            created_at=at,
            updated_at=at,
        )
        session.add(conversation)
        session.flush()
        conversation_scope.bind(session, sid, profile.child_id)
        story = StoryRecord(
            id=story_id,
            user_id=user.id,
            session_id=sid,
            category=category,
            topic_id=None,
            topic_title=title,
            title=title,
            summary=summary,
            body=f"{initial}\n\n{evidence}\n\n{reflection}",
            thought_journey={
                "initialIdea": initial,
                "evidence": [evidence],
                "alternatives": ["다른 방법도 생각해 보았다."],
                "finalReflection": reflection,
            },
            source="fallback",
            favorite=index in (0, 2),
            version=1,
            created_at=at,
            updated_at=at,
        )
        session.add(story)
        session.flush()
        conversation.result = {"status": "COMPLETED", "story": story_out(story).model_dump(mode="json", by_alias=True)}
        # 예시 대화임을 서버 메타데이터에 명시한다. 실제 AI 호출/활동 시간으로 위장하지 않는다.
        for seq, (role, content) in enumerate((("USER", initial), ("ASSISTANT", evidence), ("USER", reflection)), 1):
            session.add(
                ConversationMessage(
                    id=row_id(profile_id, "msg", f"{index}-{seq}"),
                    session_id=sid,
                    seq=seq,
                    role=role,
                    content=content,
                    source="fallback" if role == "ASSISTANT" else "child",
                    answer={"type": "TEXT"},
                    meta={
                        "screenshotSeed": SEED,
                        "valid": role == "USER",
                        "dims": ["IDEA"] if seq == 1 else ["REASON", "ALTERNATIVE", "REFLECTION"] if seq == 3 else [],
                    },
                    created_at=at,
                )
            )
        created["stories"].append(story_id)
    for index, (word, meaning, example, my_sentence, status, story_index) in enumerate(WORDS):
        if session.scalar(
            select(WordbookEntry).where(WordbookEntry.profile_id == profile_id, WordbookEntry.word == word)
        ):
            continue
        at = now - timedelta(days=index % 7)
        entry_id = row_id(profile_id, "wbe", word)
        session.add(
            WordbookEntry(
                id=entry_id,
                profile_id=profile_id,
                user_id=user.id,
                word=word,
                meaning=meaning,
                example=example,
                my_sentence=my_sentence,
                status=status,
                source="fallback",
                source_conversation_id=row_id(profile_id, "cnv", str(story_index)),
                source_sentence=example,
                last_reviewed_at=at if status != "NEW" else None,
                next_review_at=now + timedelta(days=1),
                created_at=at,
                updated_at=at,
            )
        )
        created["words"].append(entry_id)
    for index, (title, intro, emoji) in enumerate(
        (
            ("작은 발견, 커다란 상상", "하늘과 씨앗, 달 놀이터에서 찾은 세 가지 발견을 모았어요.", "🌱"),
            ("한 뼘 더 자란 내 생각", "도전하는 용기와 친구의 마음, 지구를 위한 작은 약속을 담았어요.", "🌈"),
        )
    ):
        book_id = row_id(profile_id, "bok", str(index))
        if session.get(StoryBook, book_id):
            continue
        session.add(
            StoryBook(
                id=book_id,
                profile_id=profile_id,
                user_id=user.id,
                title=title,
                introduction=intro,
                introduction_source="fallback",
                cover={"theme": "NATURE", "emoji": emoji},
                status="COMPLETED",
                version=1,
                completed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        for position in range(3):
            session.add(
                StoryBookItem(
                    id=row_id(profile_id, "bki", f"{index}-{position}"),
                    book_id=book_id,
                    story_id=row_id(profile_id, "sty", str(index * 3 + position)),
                    position=position,
                    created_at=now,
                )
            )
        created["books"].append(book_id)
    session.flush()
    return {
        "profileId": profile.id,
        "nickname": profile.nickname,
        "seed": SEED,
        "created": created,
        "counts": {key: len(value) for key, value in created.items()},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    # 운영 연결을 잘못 채우지 않도록 이 도구는 로컬 SQLite에만 사용한다.
    if db.engine().url.get_backend_name() != "sqlite":
        parser.error("이 스크린샷 도구는 로컬 SQLite 전용입니다")
    with next(db.get_session()) as session:
        result = seed_library(session, args.profile_id)
        if args.apply:
            session.commit()
        else:
            session.rollback()
        print(json.dumps({"applied": args.apply, **result}, ensure_ascii=True))


if __name__ == "__main__":
    main()
