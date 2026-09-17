"""JJCP API v1 티키 대화 프롬프트 — 티키와 첫인사, 티키와 이야기.

말하기·안전 규칙은 기존 생각 친구 대화(`talk.base_rules`)와 티키 말투(`path.TIKI_VOICE`)를 그대로 쓴다.
무엇을 물을지·언제 끝낼 수 있는지는 `app/v1/greeting_engine.py`·`story_engine.py` 의 결정론 코드가 정한다.
AI 는 문장과 판정 신호만 만든다.
"""

from __future__ import annotations

import json

from .path import TIKI_VOICE
from .talk import MOVE_GUIDE, _topic_block, base_rules

TIKI_BASE = base_rules("티키", "초등학교 1~3학년(7~9살)") + "\n" + TIKI_VOICE + "\n"

GREETING_LABELS: dict[str, str] = {
    "NICKNAME": "불러 줄 별명",
    "GRADE_OR_AGE": "학년이나 나이",
    "INTEREST": "좋아하는 것",
    "INTEREST_DETAIL": "좋아하는 까닭이나 기억나는 경험",
    "GROWTH_GOAL": "티키와 이야기하며 키우고 싶은 힘",
}

END_INTENT_RULE = "- end_intent: 아이가 대화를 그만하고 싶다고 분명히 말하면 clear, 지치거나 그만하고 싶은지 애매하면 unsure, 아니면 none"


# --- 티키와 첫인사 ------------------------------------------------------------


def greeting_instructions() -> str:
    return (
        TIKI_BASE
        + f"""
[지금 할 일: 티키와 첫인사 — 아이의 자기소개 듣기]
- 아이 말에서 아래 항목을 뽑는다. 아이가 말하지 않은 항목은 null 또는 빈 목록이다. 짐작해서 채우지 않는다.
  nickname: 불러 줄 별명(10자 이내) / grade: 초등 학년 숫자 1~6 / age: 나이(살) 숫자 /
  affiliation: elementary(초등학교) · homeschool(홈스쿨) · kindergarten(유치원) · other(그 밖) /
  interests: 좋아하는 것(짧은 낱말, 최대 5개) /
  interest_details: 좋아하는 까닭이나 기억나는 경험(아이 말에 가깝게, 한 줄 40자 이내, 최대 3개) /
  growth_goal: 티키와 이야기하며 키우고 싶은 힘이나 더 잘하고 싶은 것(20자 이내)
- 학교 이름, 주소, 실명, 생일, 전화번호, 가족 정보는 뽑지도 묻지도 않는다.
{END_INTENT_RULE}
- reaction: 아이 말에 대한 짧은 반응 한 문장(40자 이내). 질문은 넣지 않는다.
- question: [아직 모르는 항목] 가운데 이번 말에서 뽑지 못한 **첫 번째 항목 하나만** 묻는 짧은 질문(50자 이내, 물음표로 끝남).
  이미 아는 항목은 다시 묻지 않는다. 물을 항목이 없으면 빈 문자열.
- asked_field: question 이 묻는 항목 코드(NICKNAME·GRADE_OR_AGE·INTEREST·INTEREST_DETAIL·GROWTH_GOAL), 없으면 NONE
"""
    )


def greeting_input(draft: dict, missing: list[str], text: str) -> str:
    return "\n".join(
        [
            f"[지금까지 아는 것] {json.dumps(draft, ensure_ascii=False)}",
            "[아직 모르는 항목] " + (", ".join(f"{code}({GREETING_LABELS[code]})" for code in missing) or "없음"),
            f"[아이가 방금 한 말] {text}",
        ]
    )


# --- 티키와 이야기 ------------------------------------------------------------

STORY_MOVE_GUIDE: dict[str, str] = {
    "experience": "경험 묻기 — 주제와 관련해 본 적·해 본 적·알고 있는 것을 묻는다.",
    "idea": "생각 묻기 — 주제에 대한 아이 생각이나 짐작을 묻는다. 참고 질문: {hook}",
    "reason": "이유 묻기 — 방금 말한 생각의 이유나 단서를 묻는다. 선택지를 주지 않는다.",
    "alternative": MOVE_GUIDE["challenge"],
    "reflect_choice": MOVE_GUIDE["reason_check"],
    "reflect_why": "생각 돌아보기 — 처음 생각과 지금 생각을 견주어 왜 그런지 한 문장으로 말하게 한다. 선택지를 주지 않는다.",
    "imagine": MOVE_GUIDE["imagine"],
    "connect": MOVE_GUIDE["connect"],
    "tail": MOVE_GUIDE["tail"],
    "continue": "이어 가기 — 이야기를 마칠 준비가 됐다. 더 궁금한 것이나 새로운 상상을 하나 가볍게 묻는다.",
}


def story_turn_instructions() -> str:
    return (
        TIKI_BASE
        + f"""
[지금 할 일: 티키와 이야기 — 다음 대화 한 번]
- reaction: 아이 말의 핵심 표현을 짧게 인용한 공감 한 문장(40자 이내)
- question: [이번 질문 종류]에 맞는 짧은 질문 하나(60자 이내, 물음표로 끝남)
- options: 고르기만 해도 생각을 말할 수 있는 질문일 때만 짧은 선택지 2~4개(label 15자 이내, id 는 영어 대문자 코드).
  이유·설명을 묻는 질문이면 빈 목록. 정답 하나만 맞히는 퀴즈 선택지는 만들지 않는다.
- child_idea: 아이가 방금 말한 생각 한 줄 요약(없으면 빈 문자열)
- shared_experience: 아이가 경험·관찰·알고 있는 사실을 말했으면 true
- reason_given: 아이가 이유나 근거를 말했으면 true
- new_idea: 앞선 대화에 없던 다른 가능성·조건 변화·반대 경우를 말했으면 true
- stance: 처음 생각을 지켰으면 kept, 바꿨으면 changed, 아니면 none
{END_INTENT_RULE}
"""
    )


def story_turn_input(nickname: str | None, topic: dict, history: list[tuple[str, str]], move: str, text: str) -> str:
    guide = STORY_MOVE_GUIDE[move].format(
        hook=topic.get("hook", ""),
        connect=topic.get("connect", ""),
        challenge=topic.get("challenge", ""),
        imagine=topic.get("imagine", ""),
    )
    return "\n".join(
        [
            f"아이 별명: {nickname or '친구'}",
            *_topic_block(topic),
            f"[이번 질문 종류] {guide}",
            "[최근 대화]",
            *[f"{'티키' if role == 'ASSISTANT' else '아이'}: {line}" for role, line in history],
            f"[아이가 방금 한 말] {text}",
        ]
    )
