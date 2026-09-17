"""티키 말로 가르치기 — 아이 말을 프로그램으로 옮기고, 실행 결과에 반응한다.

POST /path/teach   아이 말 + 지금 프로그램 → 새 프로그램 | 되묻기 | 못 알아들음
POST /path/react   실행 결과(프론트 엔진) → 티키 반응 + (도착 시) 도전 지도 고르기

실행은 프론트 결정론 엔진이 한다. 서버는 실행하지 않는다.
안전 순서: 입력 출처 확인 → 1차 금칙어 → PII 마스킹 → LLM → 스키마·개수 → 조건 누설 → 금칙어.
AI 를 못 쓰면 정규식 순차 파서·템플릿으로 폴백한다(source="fallback", ai=false).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError

from ..prompts import path as prompt
from ..schemas.path import (
    MAX_BRANCH,
    MAX_COUNT,
    MAX_HEARD,
    MAX_STEPS,
    Clarify,
    ClarifyOption,
    Heard,
    PathReactLLM,
    PathReactRequest,
    PathReactResponse,
    PathTeachLLM,
    PathTeachRequest,
    PathTeachResponse,
    Step,
)
from ..services.llm import LlmError, call_structured
from .inquiry import _clip, _guard_origin, _prepare, _unsafe_output

router = APIRouter(tags=["path"])

# --- 설명(heard.meaning·프롬프트 공용) ---------------------------------------

_SENSOR_KO = {"front": "앞", "left": "왼쪽", "right": "오른쪽"}
_DIR_KO = {"left": "왼쪽", "right": "오른쪽"}


def _describe_leaf(node: Any) -> str:
    if node.op == "move":
        if node.until == "blocked":
            return "막히기 전까지 쭉"
        return f"앞으로 {node.count or 1}칸"
    if node.op == "turn":
        return f"{_DIR_KO.get(node.dir, '?')}으로 돌기"
    return "멈추기"


def _describe(node: Any) -> str:
    if node.op == "if":
        state = "막히면" if node.state == "blocked" else "뚫려 있으면"
        then = "·".join(_describe_leaf(n) for n in node.then) or "그냥 두기"
        text = f"{_SENSOR_KO.get(node.sensor, '?')}이 {state} {then}"
        if node.else_:
            text += ", 아니면 " + "·".join(_describe_leaf(n) for n in node.else_)
        return text
    if node.op == "repeat":
        return "우체국까지 반복: " + "·".join(_describe(n) for n in node.body)
    return _describe_leaf(node)


def _describe_all(steps: Iterable[Any]) -> str:
    return " → ".join(_describe(s) for s in steps)


# --- 프로그램 정리(LLM 출력 → API 모델) ---------------------------------------


def _clean_node(node: Any, depth: int, notes: list[str]) -> dict | None:
    """depth 0=Step, 1=Inner, 2=Leaf. 잘못된 노드는 None(버림), 넘치는 가지는 자른다."""
    out: dict[str, Any] = {"op": node.op, "count": None, "until": None, "dir": None}
    if node.op == "move":
        if node.until == "blocked":
            out["until"] = "blocked"
        elif node.count is not None:
            out["count"] = min(max(node.count, 1), MAX_COUNT)
    elif node.op == "turn":
        if node.dir is None:
            notes.append("step_dropped")
            return None
        out["dir"] = node.dir
    elif node.op == "if" and depth <= 1:
        if node.sensor is None or node.state is None:
            notes.append("step_dropped")
            return None
        then = _clean_list(node.then, 2, notes)
        other = _clean_list(node.else_, 2, notes)
        if not then and not other:
            notes.append("step_dropped")
            return None
        out.update(sensor=node.sensor, state=node.state, then=then, **{"else": other})
    elif node.op == "repeat" and depth == 0:
        body = _clean_list(node.body, 1, notes)
        if not body:
            notes.append("step_dropped")
            return None
        out["body"] = body
    elif node.op != "stop":
        notes.append("step_dropped")
        return None
    return out


def _clean_list(nodes: Iterable[Any], depth: int, notes: list[str]) -> list[dict]:
    limit = MAX_STEPS if depth == 0 else MAX_BRANCH
    cleaned = [c for c in (_clean_node(n, depth, notes) for n in nodes) if c is not None]
    if len(cleaned) > limit:
        notes.append("program_trimmed" if depth == 0 else "branch_trimmed")
    return cleaned[:limit]


def _to_steps(raw: list[dict], notes: list[str]) -> list[Step]:
    steps: list[Step] = []
    for item in raw:
        try:
            steps.append(Step.model_validate(item))
        except ValidationError:
            notes.append("step_dropped")
    return steps


# --- 조건·반복 누설 검사 -----------------------------------------------------

_STATE_WORDS = re.compile(r"막히|막혀|막힌|막혔|웅덩이|물|벽|길|뚫려|열려|비어|장애물|못 가|갈 수")
_SIDE_MENTION = re.compile(r"(왼|오른)(?:쪽|편)?\s*(?:이|가|에|도|은|는|막|뚫|열|비|길|벽|물|웅덩이)")
_REPEAT_WORDS = re.compile(r"반복|계속|되풀이|때까지|도착할 때|또\s")


def _walk_ifs(steps: Iterable[Any]) -> Iterable[Any]:
    for s in steps:
        if s.op == "if":
            yield s
        elif s.op == "repeat":
            yield from (b for b in s.body if b.op == "if")


def _guard_leaks(program: list[Step], base: list[Step], text: str, notes: list[str]) -> list[Step]:
    """아이가 말하지 않은 조건·반복은 벗겨 낸다: if → then 가지만, repeat → body 만 남긴다."""
    existing_ifs = {(s.sensor, s.state) for s in _walk_ifs(base)}
    existing_repeat = any(s.op == "repeat" for s in base)
    said_state = bool(_STATE_WORDS.search(text))
    sides = {"left" if m.group(1) == "왼" else "right" for m in _SIDE_MENTION.finditer(text)}

    def if_ok(node: Any) -> bool:
        if (node.sensor, node.state) in existing_ifs:
            return True
        return said_state and (node.sensor == "front" or node.sensor in sides)

    def unwrap_ifs(nodes: list[Any]) -> list[dict]:
        out: list[dict] = []
        for n in nodes:
            if n.op == "if" and not if_ok(n):
                notes.append("condition_removed")
                out.extend(leaf.model_dump(by_alias=True) for leaf in n.then)
            else:
                out.append(n.model_dump(by_alias=True))
        return out

    raw: list[dict] = []
    for step in program:
        if step.op == "repeat":
            body = unwrap_ifs(step.body)
            if existing_repeat or _REPEAT_WORDS.search(text):
                if body:
                    raw.append({"op": "repeat", "body": body[:MAX_BRANCH]})
            else:
                notes.append("repeat_removed")
                raw.extend(body)
        else:
            raw.extend(unwrap_ifs([step]))
    if len(raw) > MAX_STEPS:
        notes.append("program_trimmed")
    return _to_steps(raw[:MAX_STEPS], notes)


# --- 결정론 폴백 파서 --------------------------------------------------------

_NUM = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "석": 3, "네": 4, "넷": 4, "다섯": 5}
_COUNT = re.compile(r"(\d+|다섯|하나|둘|셋|넷|한|두|세|석|네)\s*(?:칸|번|걸음)")
_RESET = re.compile(r"처음부터|새로\s*시작|다\s*지우|전부\s*지우|싹\s*지우")
_SPLIT = re.compile(
    r"[\n.,!?;]+|그리고\s*나서|그리고|그\s*다음에?|다음에|그러고\s*나서|그런\s*다음에?"
    r"|\s+(?=아니면|아니라면|그렇지\s*않으면|안\s*그러면)"
)
_GO_GO = re.compile(r"(?:가|돌|멈추|걸어가|이동하)고\s+|가서\s+|가다가\s+")
_ELSE = re.compile(r"^(?:아니면|아니라면|그렇지\s*않으면|안\s*그러면|그게\s*아니면)\s*")
_REPEAT_PREFIX = re.compile(r"^(?:우체국|도착)\S*\s*(?:\S+\s*)?(?:할\s*)?때까지\s*(?:계속|반복해서|반복하면서)?\s*")
_REPEAT_SUFFIX = re.compile(
    r"\s*(?:(?:우체국|도착)\S*\s*(?:\S+\s*)?때까지\s*)?(?:(?:이걸|이거|그걸|그거|이렇게|계속)\s*)*(?:반복|되풀이)\S*\s*$"
)
_COND = re.compile(r"^(?P<cond>.+?(?:면|때는|때))(?:\s+|$)(?P<rest>.*)$")
_OPEN = re.compile(r"안\s*막|뚫려|열려|비어|길이\s*있|갈\s*수\s*있|(?:웅덩이|벽|물)\S*\s*없")
_BLOCKED = re.compile(r"막히|막혀|막힌|웅덩이|벽|물|장애물|못\s*가|갈\s*수\s*없|나오면")
_STOP = re.compile(r"멈춰|멈추|멈춤|정지|스톱|그만")
_UNTIL = re.compile(r"쭉|끝까지|막힐\s*때까지|벽까지|계속")
_SIDE = re.compile(r"(왼|오른)(?:쪽|편)?")
_TURN_ONLY = re.compile(r"돌아(?!서|가)|돌(?:고|기|자|래)?(?:\s|$)|회전|턴")
_AROUND = re.compile(r"뒤로\s*돌")
_GO = re.compile(r"앞으로|직진|전진|걸어|이동|쭉|끝까지|(?:^|\s)가(?:자|줘|요|라|봐|야|고|서)?(?:\s|$)")
_POST_OFFICE = re.compile(r"우체국")
POST_OFFICE_MEANING = "앞으로 1칸(가는 법은 아직 몰라)"


def _count(text: str) -> int | None:
    m = _COUNT.search(text)
    if not m:
        return None
    word = m.group(1)
    n = int(word) if word.isdigit() else _NUM[word]
    return min(max(n, 1), MAX_COUNT)


def _move(text: str) -> dict:
    if _UNTIL.search(text):
        return {"op": "move", "until": "blocked"}
    return {"op": "move", "count": _count(text)}


def _parse_action(text: str) -> tuple[list[dict], str | None]:
    """조건 없는 한 마디 → (Leaf 목록, 특별 뜻). 못 읽으면 빈 목록."""
    t = text.strip()
    if not t:
        return [], None
    if _STOP.search(t):
        return [{"op": "stop"}], None
    if _AROUND.search(t):
        return [{"op": "turn", "dir": "right"}, {"op": "turn", "dir": "right"}], None
    side = _SIDE.search(t)
    if side:
        turn = {"op": "turn", "dir": "left" if side.group(1) == "왼" else "right"}
        if _TURN_ONLY.search(t):
            times = _count(t) or 1
            return [dict(turn) for _ in range(min(times, MAX_BRANCH))], None
        # "오른쪽으로 가" · "웅덩이 나오면 오른쪽" = 오른쪽으로 돌고 앞으로 가기(약속)
        return [turn, _move(t)], None
    if _POST_OFFICE.search(t) and _GO.search(t) and not _COUNT.search(t) and not _UNTIL.search(t):
        return [{"op": "move"}], "post_office"
    if _GO.search(t) or _COUNT.search(t) or _UNTIL.search(t):
        return [_move(t)], None
    return [], None


def _parse_condition(clause: str) -> tuple[dict | None, str]:
    """"~면 A" → (if 노드(then 만), 조건 뒤 말). 조건이 아니면 (None, 원문)."""
    m = _COND.match(clause)
    if not m:
        return None, clause
    cond = m.group("cond")
    if _OPEN.search(cond):
        state = "open"
    elif _BLOCKED.search(cond):
        state = "blocked"
    else:
        return None, clause
    side = _SIDE.search(cond)
    sensor = "front" if side is None else ("left" if side.group(1) == "왼" else "right")
    then, _ = _parse_action(m.group("rest"))
    return {"op": "if", "sensor": sensor, "state": state, "then": then[:MAX_BRANCH], "else": []}, m.group("rest")


def _clauses(text: str) -> list[str]:
    text = _GO_GO.sub(lambda m: m.group(0).rstrip() + "\n", text)
    parts = [p.strip() for p in _SPLIT.split(text)]
    return [p for p in parts if p]


def _parse(text: str) -> tuple[list[dict], list[dict], bool, bool]:
    """결정론 폴백. (새 Step 목록, heard, 처음부터 여부, 못 읽은 조각 있음)."""
    reset = bool(_RESET.search(text))
    steps: list[dict] = []
    heard: list[dict] = []
    skipped = False
    repeat_body: list[dict] | None = None
    repeat_heard: dict | None = None

    def target() -> list[dict]:
        return repeat_body if repeat_body is not None else steps

    def add(clause: str) -> bool:
        nodes = _parse_one(clause)
        if not nodes:
            return False
        target().extend(nodes)
        post_office = any(n.pop("_post_office", False) for n in nodes)
        meaning = POST_OFFICE_MEANING if post_office else _describe_all(Step.model_validate(n) for n in nodes)
        heard.append({"phrase": clause, "meaning": meaning, "postOffice": post_office})
        return True

    for clause in _clauses(_RESET.sub(" ", text)):
        else_match = _ELSE.match(clause)
        if else_match:
            prev = target()[-1] if target() else None
            leaves, _ = _parse_action(clause[else_match.end():])
            if prev and prev["op"] == "if" and leaves and not prev["else"]:
                prev["else"] = leaves[:MAX_BRANCH]
                meaning = "아니면 " + _describe_all(Step.model_validate(n) for n in leaves)
                heard.append({"phrase": clause, "meaning": meaning})
            else:
                skipped = True
            continue
        suffix = _REPEAT_SUFFIX.search(clause)
        if suffix:
            head = clause[: suffix.start()].strip()
            if head and not add(head):
                skipped = True
            if repeat_body is None:
                # "…반복해" = 이 말에서 앞서 들은 것만 되풀이한다.
                repeat_body = [s for s in steps if s["op"] != "repeat"]
                steps = [s for s in steps if s["op"] == "repeat"]
            repeat_heard = {"phrase": clause[suffix.start():].strip(), "meaning": "우체국에 갈 때까지 반복"}
            break
        prefix = _REPEAT_PREFIX.match(clause)
        if prefix and repeat_body is None:
            repeat_body = []
            repeat_heard = {"phrase": clause[: prefix.end()].strip(), "meaning": "우체국에 갈 때까지 반복"}
            clause = clause[prefix.end():].strip()
            if not clause:
                continue
        if not add(clause):
            skipped = True

    if repeat_body:
        steps.append({"op": "repeat", "body": repeat_body[:MAX_BRANCH]})
        heard = heard[: MAX_HEARD - 1] + [repeat_heard or {"phrase": "반복", "meaning": "우체국에 갈 때까지 반복"}]
    return steps, heard, reset, skipped


def _parse_one(clause: str) -> list[dict]:
    node, rest = _parse_condition(clause)
    if node is not None:
        return [node] if node["then"] else []
    leaves, special = _parse_action(rest)
    if special == "post_office" and leaves:
        leaves[0]["_post_office"] = True
    return leaves


# --- POST /path/teach --------------------------------------------------------

TEACH_LINES = (
    "알았어! 네 말 그대로 해 볼게.",
    "좋아, 말한 만큼만 딱 할 거야. 출발!",
    "오케이, 글자 그대로 간다! 두근두근.",
    "접수! 들은 대로만 움직일게.",
)
PARTIAL_LINE = " 나머지 말은 잘 못 알아들었어."
POST_OFFICE_LINE = "우체국? 어떻게 가는지는 아직 안 알려 줬어. 일단 한 칸!"
UNMAPPED_LINES = (
    "음… 무슨 말인지 모르겠어. '앞으로 두 칸 가'처럼 말해 줄래?",
    "으앙, 머리가 빙글빙글. 티키가 할 일을 하나씩 말해 줘!",
    "그 말은 티키 사전에 없어. 가기·돌기·멈추기로 말해 줄래?",
)
BLOCKED_LINE = "그 말은 여기서 다루기 어려워. 티키한테 길을 말해 줄래?"


def _pick(lines: tuple[str, ...], attempt: int) -> str:
    return lines[max(attempt, 0) % len(lines)]


def _heard_out(items: Iterable[dict]) -> list[Heard]:
    return [Heard(phrase=_clip(h["phrase"], 20), meaning=_clip(h["meaning"], 30)) for h in items][:MAX_HEARD]


@router.post("/path/teach", response_model=PathTeachResponse)
def teach(req: PathTeachRequest) -> PathTeachResponse:
    _guard_origin(req.input_origin)
    text, blocked = _prepare(req.text, "path.teach")
    chosen = ""
    if req.pending_clarify and not blocked:
        chosen, blocked = _prepare(req.pending_clarify.chosen, "path.teach")
    base = list(req.program)

    def fallback(error: str) -> PathTeachResponse:
        def unmapped(line: str) -> PathTeachResponse:
            return PathTeachResponse(
                ai=False, source="fallback", error=error, kind="unmapped",
                program=base, heard=[], clarify=None, tiki_line=line,
            )

        if blocked:
            return unmapped(BLOCKED_LINE)
        steps, heard, reset, skipped = _parse(text)
        if not steps and chosen:
            steps, heard, reset, skipped = _parse(chosen)
        if not steps and not reset:
            return unmapped(_pick(UNMAPPED_LINES, req.attempt))
        notes: list[str] = []
        merged = ([] if reset else [s.model_dump(by_alias=True) for s in base]) + steps
        if len(merged) > MAX_STEPS:
            notes.append("program_trimmed")
        program = _to_steps(merged[:MAX_STEPS], notes)
        if reset and not steps:
            heard = [{"phrase": text, "meaning": "지금까지 말한 걸 다 지우기"}]
        if any(h.get("postOffice") for h in heard):
            line = POST_OFFICE_LINE
        else:
            line = _pick(TEACH_LINES, req.attempt) + (PARTIAL_LINE if skipped else "")
        return PathTeachResponse(
            ai=False, source="fallback", error=",".join(dict.fromkeys([error, *notes])),
            kind="program", program=program, heard=_heard_out(heard), clarify=None, tiki_line=_clip(line, 60),
        )

    if blocked:
        return fallback("blocked")
    try:
        out = call_structured(
            purpose="path.teach",
            instructions=prompt.teach_instructions(),
            user_input=prompt.teach_input(text, base, req.map_id, req.attempt, req.pending_clarify, chosen),
            schema=PathTeachLLM,
        )
    except LlmError as exc:
        return fallback(f"ai_failed:{exc.code}")

    said = f"{text} {chosen}"
    notes: list[str] = []
    heard = _heard_out({"phrase": h.phrase, "meaning": h.meaning} for h in out.heard)
    if any(_unsafe_output(f"{h.phrase} {h.meaning}") for h in heard):
        return fallback("unsafe_output")
    line = _clip(out.tiki_line, 60)

    if out.kind == "clarify":
        if req.pending_clarify is not None:
            return fallback("clarify_repeated")
        if out.clarify is None:
            return fallback("invalid_clarify")
        question = _clip(out.clarify.question, 50)
        options: list[ClarifyOption] = []
        seen: list[list[dict]] = []
        for opt in out.clarify.options:
            opt_notes: list[str] = []
            steps = _guard_leaks(_to_steps(_clean_list(opt.program, 0, opt_notes), opt_notes), base, said, opt_notes)
            dumped = [s.model_dump(by_alias=True) for s in steps]
            label = _clip(opt.label, 30)
            if not steps or not label or dumped in seen or _unsafe_output(label):
                continue
            seen.append(dumped)
            options.append(ClarifyOption(label=label, program=steps))
        if not question or len(options) < 2 or _unsafe_output(question):
            return fallback("invalid_clarify")
        if not line or _unsafe_output(line):
            line = "어? 두 가지로 들려. 어느 쪽이야?"
        return PathTeachResponse(
            ai=True, source="ai", error=None, kind="clarify", program=base, heard=heard,
            clarify=Clarify(question=question, options=options[:3]), tiki_line=line,
        )

    if out.kind == "unmapped":
        if not line or _unsafe_output(line):
            line = _pick(UNMAPPED_LINES, req.attempt)
        return PathTeachResponse(
            ai=True, source="ai", error=None, kind="unmapped", program=base, heard=[], clarify=None, tiki_line=line,
        )

    steps = _to_steps(_clean_list(out.program, 0, notes), notes)
    if out.program and not steps:
        return fallback("invalid_program")
    guarded = _guard_leaks(steps, base, said, notes)
    if steps and not guarded:
        return fallback("invalid_program")
    if "condition_removed" in notes or "repeat_removed" in notes:
        # AI 가 들은 것과 실제 프로그램이 어긋났다. 실제 프로그램 기준으로 다시 적는다.
        common = 0
        while common < min(len(base), len(guarded)) and base[common] == guarded[common]:
            common += 1
        heard = _heard_out([{"phrase": text, "meaning": _describe_all(guarded[common:]) or "바뀐 것 없음"}])
        line = _pick(TEACH_LINES, req.attempt)
    if not line or _unsafe_output(line):
        line = _pick(TEACH_LINES, req.attempt)
    return PathTeachResponse(
        ai=True, source="ai", error=",".join(dict.fromkeys(notes)) or None,
        kind="program", program=guarded, heard=heard, clarify=None, tiki_line=line,
    )


# --- POST /path/react --------------------------------------------------------

REACT_LINES: dict[str, tuple[str, ...]] = {
    "arrived": (
        "도착! 네 말대로 했더니 우체국이야. 편지 배달 완료!",
        "짠! 우체국 도착. 티키 발바닥이 뿌듯해.",
        "해냈다! 말한 그대로 갔더니 편지가 쏙 들어갔어.",
    ),
    "splashed": (
        "첨벙! 네 말대로 갔는데 웅덩이에 빠졌어. 발이 축축해…",
        "첨벙첨벙! 티키는 들은 대로 갔을 뿐인데 웅덩이였어.",
        "으악, 첨벙! 편지가 젖을 뻔했어.",
    ),
    "bumped": (
        "쿵! 벽에 콩 부딪혔어. 티키 이마가 얼얼해.",
        "쿵! 말한 대로 갔더니 벽이 딱 있었어.",
        "아야, 쿵! 벽이 길을 막고 있었어.",
    ),
    "ended": (
        "말이 끝나서 멈췄어. 그런데 여긴 우체국이 아니야.",
        "들은 걸 다 했는데… 우체국은 아직 저기 있어.",
        "끝! 근데 편지는 아직 티키 손에 있어.",
    ),
    "loop": (
        "빙글빙글… 같은 자리를 계속 돌고 있었어. 어지러워!",
        "어라, 아까 왔던 데를 또 왔어. 뱅글뱅글이야.",
        "티키가 같은 길을 뱅뱅 돌았어. 끝이 안 나!",
    ),
    "tooLong": (
        "헉헉, 너무 오래 걸었어. 티키 다리가 후들후들해.",
        "걷고 또 걸었는데 끝이 안 나서 쉬는 중이야.",
        "너무 멀리 돌아다녀서 티키가 지쳤어.",
    ),
}
REACT_QUESTIONS: dict[str, tuple[str, ...]] = {
    "arrived": ("네 말 중에 어떤 말이 제일 큰일을 했어?", "다른 지도에서도 이 말이 통할까?"),
    "splashed": ("티키가 어디서 첨벙했는지 봤어?", "티키는 네 말을 어떻게 알아들었을까?"),
    "bumped": ("티키가 어디서 쿵 했는지 봤어?", "티키는 네 말을 어떻게 알아들었을까?"),
    "ended": ("티키가 어디서 멈췄는지 봤어?", "말이 끝났을 때 티키는 어디 있었어?"),
    "loop": ("티키가 어느 길을 돌고 또 돌았을까?", "티키는 네 말을 어떻게 알아들었을까?"),
    "tooLong": ("티키가 어디를 오래 걸었는지 봤어?", "티키는 네 말을 어떻게 알아들었을까?"),
}
SAME_AGAIN_LINE = "아까랑 똑같은 말이라 티키도 똑같이 했어!"
CHALLENGE_LINES = (
    "히히, 이번엔 이 지도야! 네 말대로 하면 과연 도착할까?",
    "좋아, 티키의 도전! 이 지도에서도 네 말이 통할까?",
    "다음 지도 등장! 똑같이 말해도 괜찮을지 두고 보자, 히히.",
)

_SUGGEST = re.compile(
    r"(?:가|돌아|돌려|바꿔|넣어|빼|고쳐|추가해|말해|해)\s*(?:봐|보자|볼래|보는\s*건|야\s*해|야지|면\s*돼)"
    r"|면\s*어때|면\s*어떨까|는\s*건\s*어때"
)
_CUE = re.compile(r"오른|왼|앞으로|뒤로|막히|막혀|웅덩이|벽|면|때|까지|반복|계속|쭉|칸|멈")
_CLAIMS_ARRIVAL = re.compile(r"도착했|도착!|성공|해냈|배달 완료")


def _leaks_fix(text: str) -> bool:
    return bool(_SUGGEST.search(text) and _CUE.search(text))


@router.post("/path/react", response_model=PathReactResponse)
def react(req: PathReactRequest) -> PathReactResponse:
    _guard_origin(req.input_origin)
    text, blocked = _prepare(req.text, "path.react") if req.text.strip() else ("", False)
    result = req.result
    candidates = req.challenge_candidates
    ids = [c.id for c in candidates]

    def template_line() -> str:
        if (
            not result.changed_since_last
            and result.previous_outcome == result.outcome
            and result.outcome != "arrived"
        ):
            return SAME_AGAIN_LINE
        line = _pick(REACT_LINES[result.outcome], req.attempt)
        if result.stop_step_label and result.outcome in ("splashed", "bumped"):
            line = f"'{_clip(result.stop_step_label, 20)}' 하다가 {line}"
        return _clip(line, 80)

    def fallback(error: str) -> PathReactResponse:
        return PathReactResponse(
            ai=False, source="fallback", error=error,
            tiki_line=template_line(),
            question=_pick(REACT_QUESTIONS[result.outcome], req.attempt),
            challenge_id=ids[0] if ids else None,
            challenge_line=_pick(CHALLENGE_LINES, req.attempt) if ids else None,
        )

    if blocked:
        return fallback("blocked")
    try:
        out = call_structured(
            purpose="path.react",
            instructions=prompt.react_instructions(),
            user_input=prompt.react_input(text, req.program, req.map_id, req.attempt, result, candidates),
            schema=PathReactLLM,
        )
    except LlmError as exc:
        return fallback(f"ai_failed:{exc.code}")

    notes: list[str] = []
    line = _clip(out.tiki_line, 80)
    invented = result.outcome != "arrived" and bool(_CLAIMS_ARRIVAL.search(line))
    if not line or _unsafe_output(line) or _leaks_fix(line) or invented:
        notes.append("line_replaced")
        line = template_line()

    question = _clip(out.question, 50) if out.question else None
    if question and (_unsafe_output(question) or _leaks_fix(question)):
        notes.append("question_replaced")
        question = _pick(REACT_QUESTIONS[result.outcome], req.attempt)

    challenge_id: str | None = None
    challenge_line: str | None = None
    if ids:
        challenge_id = out.challenge_id if out.challenge_id in ids else ids[0]
        challenge_line = _clip(out.challenge_line, 80) if out.challenge_line else ""
        if challenge_id != out.challenge_id:
            notes.append("challenge_replaced")
            challenge_line = ""
        if not challenge_line or _unsafe_output(challenge_line) or _leaks_fix(challenge_line):
            if challenge_line:
                notes.append("challenge_line_replaced")
            challenge_line = _pick(CHALLENGE_LINES, req.attempt)
    return PathReactResponse(
        ai=True, source="ai", error=",".join(dict.fromkeys(notes)) or None,
        tiki_line=line, question=question, challenge_id=challenge_id, challenge_line=challenge_line,
    )
