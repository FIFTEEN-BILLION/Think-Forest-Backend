"""티키 말로 가르치기(teach·react) API 계약·안전 테스트. OpenAI 호출은 mock 한다."""

import pytest
from app.main import app
from app.routers import path as path_router
from app.schemas.path import PathReactLLM, PathTeachLLM
from app.services.llm import LlmError
from fastapi.testclient import TestClient

client = TestClient(app)
NO_KEY = LlmError("no_api_key", "x")


@pytest.fixture
def fake_llm(monkeypatch):
    calls: list[dict] = []

    def install(result):
        def fake(**kwargs):
            calls.append(kwargs)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(path_router, "call_structured", fake)
        return calls

    return install


# --- 도우미 --------------------------------------------------------------------


def leaf(op, count=None, until=None, dir=None):
    return {"op": op, "count": count, "until": until, "dir": dir}


def llm_step(op, *, count=None, until=None, dir=None, sensor=None, state=None, then=(), other=(), body=()):
    return {
        **leaf(op, count, until, dir),
        "sensor": sensor,
        "state": state,
        "then": list(then),
        "else": list(other),
        "body": [{k: v for k, v in b.items() if k != "body"} for b in body],
    }


def teach_out(kind="program", program=(), heard=None, clarify=None, tiki_line="알았어! 그대로 할게."):
    return PathTeachLLM.model_validate(
        {
            "kind": kind,
            "program": list(program),
            "heard": heard if heard is not None else [{"phrase": "앞으로 가", "meaning": "앞으로 1칸"}],
            "clarify": clarify,
            "tiki_line": tiki_line,
        }
    )


def react_out(tiki_line="첨벙! 웅덩이에 빠졌어.", question="티키가 어디서 첨벙했는지 봤어?", cid=None, cline=None):
    return PathReactLLM(tiki_line=tiki_line, question=question, challenge_id=cid, challenge_line=cline)


def teach(text, program=None, **extra):
    return client.post("/path/teach", json={"text": text, "program": program or [], "mapId": "m1", **extra})


def react(outcome="splashed", candidates=None, text="쭉 가", **result):
    body = {
        "text": text,
        "program": [{"op": "move", "until": "blocked"}],
        "mapId": "m1",
        "attempt": 1,
        "result": {"outcome": outcome, "moves": 2, "changedSinceLast": True, **result},
        "challengeCandidates": candidates or [],
    }
    return client.post("/path/react", json=body)


def ops(program):
    """비교하기 쉬운 모양: (op, count, until, dir, sensor, state, then ops, else ops, body ops)."""
    out = []
    for s in program:
        out.append(
            (
                s["op"], s["count"], s["until"], s["dir"], s["sensor"], s["state"],
                [(t["op"], t["count"], t["until"], t["dir"]) for t in s["then"]],
                [(t["op"], t["count"], t["until"], t["dir"]) for t in s["else"]],
                [(b["op"], b["sensor"], b["state"]) for b in s["body"]],
            )
        )
    return out


def simple(program):
    return [(s["op"], s["count"], s["until"], s["dir"]) for s in program]


# --- teach: 폴백 파서 -----------------------------------------------------------


def test_fallback_straight_until_blocked(fake_llm):
    fake_llm(NO_KEY)
    body = teach("쭉 가").json()
    assert body["ai"] is False and body["source"] == "fallback"
    assert body["error"] == "ai_failed:no_api_key"
    assert body["kind"] == "program" and body["clarify"] is None
    assert simple(body["program"]) == [("move", None, "blocked", None)]
    assert body["heard"] == [{"phrase": "쭉 가", "meaning": "막히기 전까지 쭉"}]
    assert body["tikiLine"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("앞으로 두 칸 가", [("move", 2, None, None)]),
        ("3칸 가 그 다음에 왼쪽으로 돌아", [("move", 3, None, None), ("turn", None, None, "left")]),
        ("끝까지 가", [("move", None, "blocked", None)]),
        ("오른쪽으로 돌아", [("turn", None, None, "right")]),
        ("오른쪽으로 가", [("turn", None, None, "right"), ("move", None, None, None)]),
        ("멈춰", [("stop", None, None, None)]),
        (
            "앞으로 가고 오른쪽으로 돌고 두 칸 가",
            [("move", None, None, None), ("turn", None, None, "right"), ("move", 2, None, None)],
        ),
    ],
)
def test_fallback_sequential_phrases(fake_llm, text, expected):
    fake_llm(NO_KEY)
    body = teach(text).json()
    assert simple(body["program"]) == expected


def test_fallback_go_right_is_turn_then_move_with_one_heard(fake_llm):
    fake_llm(NO_KEY)
    body = teach("오른쪽으로 가").json()
    assert body["heard"] == [{"phrase": "오른쪽으로 가", "meaning": "오른쪽으로 돌기 → 앞으로 1칸"}]


def test_fallback_if_else(fake_llm):
    fake_llm(NO_KEY)
    body = teach("웅덩이가 있으면 오른쪽으로 돌아, 아니면 앞으로 가").json()
    assert ops(body["program"]) == [
        ("if", None, None, None, "front", "blocked", [("turn", None, None, "right")], [("move", None, None, None)], [])
    ]
    assert [h["phrase"] for h in body["heard"]] == ["웅덩이가 있으면 오른쪽으로 돌아", "아니면 앞으로 가"]


def test_fallback_if_side_sensor_and_bare_direction(fake_llm):
    fake_llm(NO_KEY)
    body = teach("오른쪽이 막히면 멈춰").json()
    assert ops(body["program"])[0][4:7] == ("right", "blocked", [("stop", None, None, None)])
    body = teach("웅덩이 나오면 오른쪽").json()
    assert ops(body["program"])[0][4:7] == (
        "front", "blocked", [("turn", None, None, "right"), ("move", None, None, None)]
    )


def test_fallback_repeat_suffix_wraps_what_was_said(fake_llm):
    fake_llm(NO_KEY)
    body = teach("앞으로 가. 막히면 왼쪽으로 돌아. 우체국 갈 때까지 반복해").json()
    assert ops(body["program"]) == [
        ("repeat", None, None, None, None, None, [], [], [("move", None, None), ("if", "front", "blocked")])
    ]
    assert body["heard"][-1]["meaning"] == "우체국에 갈 때까지 반복"


def test_fallback_repeat_prefix(fake_llm):
    fake_llm(NO_KEY)
    body = teach("우체국 갈 때까지 앞으로 가").json()
    assert ops(body["program"]) == [("repeat", None, None, None, None, None, [], [], [("move", None, None)])]


def test_fallback_appends_to_program_and_resets_on_request(fake_llm):
    fake_llm(NO_KEY)
    current = [{"op": "move", "count": 2}]
    body = teach("오른쪽으로 돌아", current).json()
    assert simple(body["program"]) == [("move", 2, None, None), ("turn", None, None, "right")]
    body = teach("처음부터 다시 앞으로 한 칸 가", current).json()
    assert simple(body["program"]) == [("move", 1, None, None)]


def test_fallback_post_office_without_method(fake_llm):
    fake_llm(NO_KEY)
    body = teach("우체국으로 가").json()
    assert simple(body["program"]) == [("move", None, None, None)]
    assert "몰라" in body["heard"][0]["meaning"]
    assert "안 알려 줬어" in body["tikiLine"]


def test_fallback_unmapped_keeps_program(fake_llm):
    fake_llm(NO_KEY)
    current = [{"op": "move", "count": 2}]
    body = teach("티키야 안녕 반가워", current).json()
    assert body["kind"] == "unmapped" and body["heard"] == []
    assert simple(body["program"]) == [("move", 2, None, None)]


def test_fallback_trims_to_eight_steps(fake_llm):
    fake_llm(NO_KEY)
    current = [{"op": "move"}] * 7
    body = teach("오른쪽으로 가", current).json()
    assert len(body["program"]) == 8
    assert "program_trimmed" in body["error"]


# --- teach: AI 경로 검증 ----------------------------------------------------------


def test_ai_program_passthrough(fake_llm):
    fake_llm(teach_out(program=[llm_step("move", until="blocked")], heard=[{"phrase": "쭉 가", "meaning": "쭉"}]))
    body = teach("쭉 가").json()
    assert body["ai"] is True and body["source"] == "ai" and body["error"] is None
    assert simple(body["program"]) == [("move", None, "blocked", None)]
    assert body["tikiLine"] == "알았어! 그대로 할게."


def test_ai_clarify_options_are_validated(fake_llm):
    clarify = {
        "question": "우체국으로 어떻게 가? 티키는 길을 몰라",
        "options": [
            {"label": "앞으로 한 칸", "program": [llm_step("move", count=1)]},
            {"label": "막힐 때까지 쭉", "program": [llm_step("move", until="blocked")]},
            {"label": "돌기", "program": [llm_step("turn")]},  # dir 없음 → 버림
        ],
    }
    fake_llm(teach_out(kind="clarify", program=[llm_step("stop")], clarify=clarify))
    current = [{"op": "move", "count": 2}]
    body = teach("우체국으로 가", current).json()
    assert body["ai"] is True and body["kind"] == "clarify"
    assert simple(body["program"]) == [("move", 2, None, None)]  # 요청 program 그대로
    options = body["clarify"]["options"]
    assert [o["label"] for o in options] == ["앞으로 한 칸", "막힐 때까지 쭉"]
    assert simple(options[1]["program"]) == [("move", None, "blocked", None)]


def test_ai_clarify_with_too_few_valid_options_falls_back(fake_llm):
    clarify = {"question": "어떻게 가?", "options": [{"label": "쭉", "program": [llm_step("move", until="blocked")]}]}
    fake_llm(teach_out(kind="clarify", clarify=clarify))
    body = teach("쭉 가").json()
    assert body["ai"] is False and body["error"] == "invalid_clarify"
    assert simple(body["program"]) == [("move", None, "blocked", None)]


def test_ai_clarify_again_after_answer_falls_back(fake_llm):
    clarify = {
        "question": "또 물어볼게",
        "options": [
            {"label": "a", "program": [llm_step("move")]},
            {"label": "b", "program": [llm_step("stop")]},
        ],
    }
    fake_llm(teach_out(kind="clarify", clarify=clarify))
    body = teach("쭉", pendingClarify={"question": "어떻게 가?", "chosen": "막힐 때까지 쭉 가기"}).json()
    assert body["error"] == "clarify_repeated" and body["kind"] == "program"
    assert simple(body["program"]) == [("move", None, "blocked", None)]


def test_ai_condition_leak_is_removed(fake_llm):
    leaked = llm_step("if", sensor="right", state="open", then=[leaf("move")])
    fake_llm(teach_out(program=[leaked], heard=[{"phrase": "앞으로 가", "meaning": "오른쪽이 뚫려 있으면 가기"}]))
    body = teach("앞으로 가").json()
    assert body["ai"] is True
    assert simple(body["program"]) == [("move", None, None, None)]
    assert "condition_removed" in body["error"]
    assert body["heard"] == [{"phrase": "앞으로 가", "meaning": "앞으로 1칸"}]


def test_ai_side_condition_needs_side_word_but_existing_condition_is_kept(fake_llm):
    front = llm_step("if", sensor="front", state="blocked", then=[leaf("turn", dir="right"), leaf("move")])
    right = llm_step("if", sensor="right", state="open", then=[leaf("turn", dir="right")])
    fake_llm(teach_out(program=[front, right]))
    body = teach("웅덩이 나오면 오른쪽").json()
    assert [s["sensor"] for s in body["program"]] == ["front", None]
    assert "condition_removed" in body["error"]

    existing = [{"op": "if", "sensor": "right", "state": "open", "then": [{"op": "turn", "dir": "right"}]}]
    fake_llm(teach_out(program=[right, llm_step("move", count=2)]))
    body = teach("앞으로 두 칸 가", existing).json()
    assert body["error"] is None
    assert [s["op"] for s in body["program"]] == ["if", "move"]


def test_ai_repeat_leak_is_removed(fake_llm):
    fake_llm(teach_out(program=[llm_step("repeat", body=[llm_step("move")])]))
    body = teach("앞으로 가").json()
    assert simple(body["program"]) == [("move", None, None, None)]
    assert "repeat_removed" in body["error"]


def test_ai_program_over_eight_steps_is_trimmed(fake_llm):
    fake_llm(teach_out(program=[llm_step("move")] * 10))
    body = teach("앞으로 가 열 번").json()
    assert len(body["program"]) == 8
    assert "program_trimmed" in body["error"]


def test_ai_invalid_steps_dropped_and_counts_clamped(fake_llm):
    branch = [leaf("move")] * 6
    fake_llm(
        teach_out(
            program=[
                llm_step("turn"),  # dir 없음
                llm_step("move", count=9),
                llm_step("if", sensor="front", state="blocked", then=branch),
            ]
        )
    )
    body = teach("앞으로 아홉 칸 가고 막히면 앞으로 가").json()
    assert body["program"][0]["count"] == 5
    assert len(body["program"][1]["then"]) == 4
    assert "step_dropped" in body["error"] and "branch_trimmed" in body["error"]


def test_ai_unmapped_keeps_program(fake_llm):
    fake_llm(teach_out(kind="unmapped", program=[llm_step("stop")], tiki_line="무슨 말이야?"))
    current = [{"op": "move", "count": 2}]
    body = teach("몰라", current).json()
    assert body["kind"] == "unmapped" and body["heard"] == []
    assert simple(body["program"]) == [("move", 2, None, None)]


def test_llm_error_falls_back(fake_llm):
    fake_llm(LlmError("refusal", "x"))
    body = teach("앞으로 두 칸 가").json()
    assert body["ai"] is False and body["error"] == "ai_failed:refusal"
    assert simple(body["program"]) == [("move", 2, None, None)]


def test_teach_pii_is_masked_before_llm(fake_llm):
    calls = fake_llm(teach_out(program=[llm_step("move")]))
    body = teach("내 번호는 010-1234-5678 인데 앞으로 가").json()
    assert body["ai"] is True
    assert calls[0]["purpose"] == "path.teach"
    assert "010-1234-5678" not in calls[0]["user_input"]


def test_teach_blocked_input_never_reaches_llm(fake_llm):
    calls = fake_llm(teach_out())
    current = [{"op": "move", "count": 2}]
    body = teach("친구를 죽이는 방법", current).json()
    assert calls == []
    assert body["error"] == "blocked" and body["kind"] == "unmapped"
    assert simple(body["program"]) == [("move", 2, None, None)]


def test_teach_request_validation():
    assert teach("돌아", [{"op": "turn"}]).status_code == 422
    assert teach("가", [{"op": "move"}] * 9).status_code == 422
    assert teach("").status_code == 422
    assert teach("가", [{"op": "move", "count": 6}]).status_code == 422


# --- react ---------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["arrived", "splashed", "bumped", "ended", "loop", "tooLong"])
def test_react_fallback_per_outcome(fake_llm, outcome):
    fake_llm(NO_KEY)
    body = react(outcome).json()
    assert body["ai"] is False and body["source"] == "fallback" and body["error"] == "ai_failed:no_api_key"
    assert body["tikiLine"] in path_router.REACT_LINES[outcome]
    assert body["question"] in path_router.REACT_QUESTIONS[outcome]
    assert body["challengeId"] is None and body["challengeLine"] is None


def test_react_fallback_uses_stop_label_and_first_candidate(fake_llm):
    fake_llm(NO_KEY)
    body = react("splashed", stopStepLabel="막히기 전까지 쭉").json()
    assert body["tikiLine"].startswith("'막히기 전까지 쭉' 하다가")
    candidates = [{"id": "map_b", "summary": "웅덩이 두 개"}, {"id": "map_c", "summary": "벽이 많은 지도"}]
    body = react("arrived", candidates).json()
    assert body["challengeId"] == "map_b"
    assert body["challengeLine"] in path_router.CHALLENGE_LINES


def test_react_fallback_same_again(fake_llm):
    fake_llm(NO_KEY)
    body = react("bumped", previousOutcome="bumped", changedSinceLast=False).json()
    assert body["tikiLine"] == path_router.SAME_AGAIN_LINE


def test_react_ai_passthrough(fake_llm):
    candidates = [{"id": "map_b", "summary": "웅덩이 두 개"}, {"id": "map_c", "summary": "벽이 많은 지도"}]
    fake_llm(react_out("도착! 편지 배달 완료.", None, "map_c", "히히, 이번엔 이 지도야! 과연?"))
    body = react("arrived", candidates).json()
    assert body["ai"] is True and body["error"] is None
    assert body["tikiLine"] == "도착! 편지 배달 완료."
    assert body["question"] is None
    assert body["challengeId"] == "map_c" and body["challengeLine"] == "히히, 이번엔 이 지도야! 과연?"


def test_react_unknown_challenge_id_uses_first_candidate(fake_llm):
    candidates = [{"id": "map_b", "summary": "웅덩이 두 개"}, {"id": "map_c", "summary": "벽이 많은 지도"}]
    fake_llm(react_out("도착!", None, "map_zzz", "이 지도는 왼쪽 벽이 약점이야"))
    body = react("arrived", candidates).json()
    assert body["challengeId"] == "map_b"
    assert body["challengeLine"] in path_router.CHALLENGE_LINES
    assert "challenge_replaced" in body["error"]


def test_react_no_candidates_means_no_challenge(fake_llm):
    fake_llm(react_out("도착!", None, "map_b", "이번엔 이 지도야!"))
    body = react("arrived").json()
    assert body["challengeId"] is None and body["challengeLine"] is None


def test_react_leak_phrases_are_replaced(fake_llm):
    fake_llm(react_out("첨벙! 다음엔 오른쪽으로 돌아 봐", "막히면 왼쪽으로 가 보는 건 어때?"))
    body = react("splashed").json()
    assert body["ai"] is True
    assert body["tikiLine"] in path_router.REACT_LINES["splashed"]
    assert body["question"] in path_router.REACT_QUESTIONS["splashed"]
    assert "line_replaced" in body["error"] and "question_replaced" in body["error"]


def test_react_challenge_line_leak_is_replaced(fake_llm):
    candidates = [{"id": "map_b", "summary": "웅덩이 두 개"}]
    fake_llm(react_out("도착!", None, "map_b", "이번엔 막히면 오른쪽으로 가 봐!"))
    body = react("arrived", candidates).json()
    assert body["challengeId"] == "map_b"
    assert body["challengeLine"] in path_router.CHALLENGE_LINES
    assert "challenge_line_replaced" in body["error"]


def test_react_invented_arrival_is_replaced(fake_llm):
    fake_llm(react_out("도착했어! 편지 배달 완료!"))
    body = react("bumped").json()
    assert body["tikiLine"] in path_router.REACT_LINES["bumped"]


def test_react_pii_is_masked_before_llm(fake_llm):
    calls = fake_llm(react_out())
    body = react("splashed", text="우리 집 전화 010-1234-5678 쭉 가").json()
    assert body["ai"] is True
    assert calls[0]["purpose"] == "path.react"
    assert "010-1234-5678" not in calls[0]["user_input"]


@pytest.mark.parametrize("endpoint", ["teach", "react"])
def test_demo_mode_rejects_child_origin(fake_llm, endpoint):
    calls = fake_llm(NO_KEY)
    if endpoint == "teach":
        r = teach("쭉 가", inputOrigin="child")
    else:
        r = client.post(
            "/path/react",
            json={"text": "쭉 가", "inputOrigin": "child", "result": {"outcome": "ended", "moves": 1}},
        )
    assert r.status_code == 403
    assert calls == []


def test_interpret_endpoint_is_gone():
    assert client.post("/path/interpret", json={"text": "앞으로 가"}).status_code in (404, 405)
