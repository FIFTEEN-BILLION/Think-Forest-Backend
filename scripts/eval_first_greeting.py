"""실제 모델로 첫인사 의미 해석을 평가한다. 합성 문장만 사용하며 계정/대화 DB에는 쓰지 않는다.

실행: .venv/Scripts/python.exe -m scripts.eval_first_greeting --live
API 비용이 발생하므로 일반 pytest에는 포함하지 않는다.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor

from app.prompts.v1_conversation import greeting_input, greeting_instructions
from app.services.llm import call_structured
from app.v1.greeting_engine import empty_draft, missing_fields
from app.v1.greeting_profile import apply_changes
from app.v1.schemas_conversation import FirstGreetingLLM

ALIEN = "나는 외계 32행성에서 온 외계인 삐리빠라 3세야 나를 삐리빠라 3세에서 3세는 빼고 불러도 돼"


def evaluate(case: str) -> dict:
    draft, deferred, memory, history = empty_draft(), [], "", []
    messages = {
        "alien": [ALIEN, "우리 행성에서는 운석 던지기를 하고 놀아"],
        "uncertain": [
            "나는 감자일까 아닐까? 너가 맞춰보세요 저는 숭실초 3학년이 아닙니다 "
            "그럼 어디 초 무슨 학년일까요? 제가 좋아하는것은 아직 말 안할거에용"
        ],
        "correction": ["3학년이 아니라 4학년이야. 공룡은 이제 안 좋아하고 축구를 좋아해."],
        "defer": ["좋아하는 건 지금 말 안 할래"],
        "phrases": ["나는 그림 그리기랑 강아지를 좋아해"],
    }[case]
    if case in {"correction", "defer"}:
        draft.update(nickname="별", gradeOrAgeBand="3학년", interests=["공룡"], interestDetails=["큰 이빨이 멋있어"])
    replies = []
    for index, text in enumerate(messages):
        current = f"synthetic-{case}-{index}"
        history.append({"id": current, "role": "USER", "content": text})
        out = call_structured(
            purpose="eval.first_greeting",
            instructions=greeting_instructions(),
            user_input=greeting_input(
                draft=draft,
                missing=missing_fields(draft),
                deferred=deferred,
                context_summary=memory,
                history=history,
                current_message_id=current,
                event="MESSAGE",
            ),
            schema=FirstGreetingLLM,
            max_output_tokens=4000,
        )
        draft, deferred, _ = apply_changes(
            draft,
            deferred,
            out,
            messages={m["id"]: m["content"] for m in history if m["role"] == "USER"},
            current_id=current,
        )
        memory = out.context_summary
        history.append({"id": f"assistant-{index}", "role": "ASSISTANT", "content": out.message})
        replies.append(out.message)
    if case == "alien":
        assert draft == {**empty_draft(), "nickname": "삐리빠라"}, draft
    elif case == "uncertain":
        assert draft == empty_draft() and "interests" in deferred, (draft, deferred)
    elif case == "correction":
        assert draft["gradeOrAgeBand"] == "4학년" and draft["interests"] == ["축구"] and not draft["interestDetails"], (
            draft
        )
    elif case == "defer":
        assert draft["interests"] == ["공룡"] and "interests" in deferred, (draft, deferred)
    elif case == "phrases":
        assert set(draft["interests"]) == {"그림 그리기", "강아지"}, draft
    return {"case": case, "passed": True, "draft": draft, "replies": replies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="합성 입력을 실제 AI에 보내 유료 평가")
    args = parser.parse_args()
    if not args.live:
        parser.error("실제 API 호출에는 --live가 필요합니다")
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            case: pool.submit(evaluate, case) for case in ("alien", "uncertain", "correction", "defer", "phrases")
        }
        failed = False
        for case, future in futures.items():
            try:
                result = future.result()
            except Exception as exc:
                result = {"case": case, "passed": False, "error": str(exc)}
                failed = True
            print(json.dumps(result, ensure_ascii=True), flush=True)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
