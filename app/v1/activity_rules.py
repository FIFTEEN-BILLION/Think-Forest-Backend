"""활동 단계 조건 — 프런트 `lib/learning.ts` 의 `guard`·`transition`·`readyToComplete` 를 서버에 그대로 옮긴 것.

화면의 버튼 비활성화는 도움일 뿐이다. 다음 단계로 갈 수 있는지와 완료할 수 있는지는 여기서만 정한다.
판정에 쓰는 값(`state`)은 전부 기록된 사건에서 나온다. 글자 수는 프런트와 같이 공백을 뺀 수를 센다.

프런트 대응표
- `count`·`writingStep`·`hasObservations`·`blockedKeyword` → 같은 이름의 함수
- `guard` → `missing()` (조건 코드 목록으로 돌려준다)
- `transition` → `apply_event()` + `advance()`
- `readyToComplete` → `ready_to_complete()`
"""

from __future__ import annotations

import re
from typing import Any

from . import activity_catalog as catalog
from .activity_catalog import INQUIRY_ACTIVITY, PATH_ACTIVITY, Activity

BLOCKED = re.compile(r"(살인|자살|자해|성폭|성관계|음란|포르노|마약|폭탄|고문|죽이|죽여)")
MAX_TEXT = 2000
MAX_OBSERVATION = 400
# 프런트가 지도 한 장을 최대 5번까지 시험해 보고 넘어가는 것과 같다(lib/path.ts stepError).
PATH_MAX_RUNS = 5

# 조건 코드 → 아이가 읽는 안내. `missing()` 이 돌려주는 순서대로 화면에 보여 준다.
MESSAGES: dict[str, str] = {
    "MIN_TEXT": "공백을 빼고 {min}자 이상, 내 생각을 먼저 적어 주세요.",
    "LAB_TOPIC": "궁금한 주제를 두 글자 이상 적어 주세요.",
    "LAB_OBSERVATIONS": "30 이하와 70 이상의 두 조건을 모두 관찰해 주세요.",
    "LAB_OBSERVATIONS_CUSTOM": "서로 다른 두 가지 관찰을 각각 세 글자 이상 적어 주세요.",
    "THEATER_KEYWORD": "이야기에 담을 마음 키워드를 두 글자 이상 적어 주세요.",
    "GUARDIAN_PREVIEW": "보호자가 대본과 두 가지 분기를 먼저 확인해 주세요.",
    "THEATER_CHOICE": "이야기를 끝까지 보고 내가 할 행동을 골라 주세요.",
    "THEATER_EMOTION": "이야기를 보며 느낀 마음을 하나 골라 주세요.",
    "INQUIRY_THOUGHT": "내 생각을 세 글자 이상 적어 주세요.",
    "INQUIRY_REASON": "그렇게 생각한 이유도 세 글자 이상 적어 주세요.",
    "INQUIRY_MEANING": "내가 말하려던 뜻을 세 글자 이상 적어 주세요.",
    "INQUIRY_CONFIRM": "내가 말하려던 뜻인지 확인해 주세요.",
    "INQUIRY_CONDITIONS": "빛이 낮을 때와 높을 때, 두 결과를 모두 살펴봐 주세요.",
    "INQUIRY_JUDGMENT": "지금 내 생각에 가까운 것을 하나 골라 주세요.",
    "PATH_RUN": "티키를 우체국까지 한 번 보내 주세요.",
    "PATH_WAITING": "티키가 도전 지도를 고르고 있어요.",
    "BLOCKED_KEYWORD": "이 주제로는 활동을 준비할 수 없어요. 다른 주제를 골라 주세요.",
    "STEP_DONE": "마지막 단계예요.",
}


class StepError(Exception):
    """단계 조건 실패. 라우터가 409 ACTIVITY_STEP_NOT_READY 로 바꾼다."""

    def __init__(self, codes: list[str], min_characters: int):
        super().__init__(codes[0] if codes else "STEP_DONE")
        self.codes = codes
        self.details = [
            {"code": code, "message": MESSAGES[code].format(min=min_characters)} for code in codes if code in MESSAGES
        ]


def count(text: str | None) -> int:
    """공백을 뺀 글자 수(프런트 `count`)."""
    return len(re.sub(r"\s", "", text or ""))


def blocked_keyword(text: str) -> bool:
    return bool(BLOCKED.search(re.sub(r"\s", "", text or "")))


def max_step(activity: Activity) -> int:
    if activity.id == PATH_ACTIVITY:
        return 1
    if activity.id == INQUIRY_ACTIVITY:
        return 4
    return {"forest": 4, "lab": 5, "theater": 4}[activity.track]


def empty_state(activity: Activity, keyword: str = "") -> dict:
    """프런트 `createDraft` 와 같은 초안. 활동에 맞는 칸만 채운다."""
    mode = "balance" if activity.id == "balance" else "custom" if activity.id == "custom" else "shadow"
    state: dict[str, Any] = {
        "title": activity.title,
        "text": "",
        "answers": [],
        "followup": "",
        "hints": 0,
        "lab": {
            "mode": mode,
            "topic": "",
            "value": 50,
            "low": False,
            "high": False,
            "a": "",
            "b": "",
            "source": "",
            "prediction": "",
        },
        "theater": {"keyword": keyword, "story": None, "scene": 0, "choice": None, "emotion": "", "approved": False},
    }
    if activity.id == INQUIRY_ACTIVITY:
        state["inquiry"] = {
            "initial": "",
            "reason": "",
            "meaning": "",
            "confirmed": False,
            "observed": [],
            "judgment": None,
            "final": "",
            "finalReason": "",
        }
    if activity.id == PATH_ACTIVITY:
        state["path"] = {"runs": 0, "wins": 0, "awaitingChallenge": False, "noChallengeLeft": False}
    return state


def writing_step(track: str, activity_id: str, step: int) -> bool:
    """이 단계가 글쓰기 단계인가(프런트 `writingStep`)."""
    if activity_id in catalog.SIMULATED:
        return False
    if track == "forest":
        return 1 <= step <= 3
    if track == "lab":
        return step in (1, 3, 4)
    return step == 3


def has_observations(lab: dict) -> bool:
    """두 조건을 모두 관찰했는가(프런트 `hasObservations`)."""
    if lab.get("mode") == "custom":
        a, b = lab.get("a", ""), lab.get("b", "")
        return count(a) >= 3 and count(b) >= 3 and re.sub(r"\s", "", a) != re.sub(r"\s", "", b)
    return bool(lab.get("low")) and bool(lab.get("high"))


def _writing_missing(thought: str, reason: str, minimum: int) -> list[str]:
    """첫 탐구의 생각+이유 글쓰기 조건(프런트 inquiry `writingError`)."""
    if count(thought) < 3:
        return ["INQUIRY_THOUGHT"]
    if count(reason) < 3:
        return ["INQUIRY_REASON"]
    return ["MIN_TEXT"] if count(thought + reason) < minimum else []


def _inquiry_missing(state: dict, step: int, minimum: int) -> list[str]:
    q = state.get("inquiry") or {}
    if step == 0:
        return _writing_missing(q.get("initial", ""), q.get("reason", ""), minimum)
    if step == 1:
        if count(q.get("meaning", "")) < 3:
            return ["INQUIRY_MEANING"]
        if not q.get("confirmed"):
            return ["INQUIRY_CONFIRM"]
    if step == 2 and not ({"low", "high"} <= set(q.get("observed") or [])):
        return ["INQUIRY_CONDITIONS"]
    if step == 3:
        if not q.get("judgment"):
            return ["INQUIRY_JUDGMENT"]
        return _writing_missing(q.get("final", ""), q.get("finalReason", ""), minimum)
    return []


def _path_missing(state: dict, step: int) -> list[str]:
    p = state.get("path") or {}
    if step != 0:
        return []
    if p.get("awaitingChallenge"):
        return ["PATH_WAITING"]
    finished = p.get("noChallengeLeft") or p.get("wins", 0) >= 1 or p.get("runs", 0) >= PATH_MAX_RUNS
    return [] if finished else ["PATH_RUN"]


def missing(activity: Activity, step: int, state: dict, minimum: int) -> list[str]:
    """지금 단계에서 아직 못 채운 조건 코드. 비어 있으면 다음 단계로 갈 수 있다(프런트 `guard`)."""
    if activity.id == PATH_ACTIVITY:
        return _path_missing(state, step)
    if activity.id == INQUIRY_ACTIVITY:
        return _inquiry_missing(state, step, minimum)
    if writing_step(activity.track, activity.id, step) and count(state.get("text")) < minimum:
        return ["MIN_TEXT"]
    lab, theater = state.get("lab") or {}, state.get("theater") or {}
    if activity.track == "lab":
        if step == 0 and lab.get("mode") == "custom" and count(lab.get("topic")) < 2:
            return ["LAB_TOPIC"]
        if step == 2 and not has_observations(lab):
            return ["LAB_OBSERVATIONS_CUSTOM" if lab.get("mode") == "custom" else "LAB_OBSERVATIONS"]
    if activity.track == "theater":
        if step == 0 and count(theater.get("keyword")) < 2:
            return ["THEATER_KEYWORD"]
        if step == 1 and not theater.get("approved"):
            return ["GUARDIAN_PREVIEW"]
        if step == 2 and (theater.get("scene") != 3 or theater.get("choice") is None):
            return ["THEATER_CHOICE"]
        if step == 3 and not theater.get("emotion"):
            return ["THEATER_EMOTION"]
    return []


def ready_to_complete(activity: Activity, step: int, state: dict, minimum: int) -> bool:
    """마지막 단계까지 왔고 필요한 답이 모두 있는가(프런트 `readyToComplete`)."""
    answers = state.get("answers") or []
    if activity.id == PATH_ACTIVITY:
        return step == 1 and not _path_missing(state, 0)
    if activity.id == INQUIRY_ACTIVITY:
        return step == 4 and all(not _inquiry_missing(state, s, minimum) for s in (0, 1, 2, 3))
    enough = all(count(a.get("text")) >= minimum for a in answers)
    if activity.track == "forest":
        return step == 4 and len(answers) == 3 and enough
    if activity.track == "lab":
        return step == 5 and has_observations(state.get("lab") or {}) and len(answers) == 3 and enough
    theater = state.get("theater") or {}
    return (
        step == 4
        and bool(theater.get("approved"))
        and theater.get("scene") == 3
        and theater.get("choice") is not None
        and bool(theater.get("emotion"))
        and len(answers) == 1
        and enough
    )


# --- 자동 저장 사건 ---------------------------------------------------------------

EVENT_TYPES = (
    "TEXT",
    "HINT",
    "TOPIC",
    "KEYWORD",
    "LAB_VALUE",
    "OBSERVATION",
    "APPROVE",
    "SCENE",
    "CHOICE",
    "EMOTION",
    "INQUIRY",
    "RUN",
)
OBSERVATION_FIELDS = {"LOW_LIGHT": "low", "HIGH_LIGHT": "high", "A": "a", "B": "b"}
INQUIRY_FIELDS = {
    "INITIAL": "initial",
    "REASON": "reason",
    "MEANING": "meaning",
    "CONFIRMED": "confirmed",
    "OBSERVED": "observed",
    "JUDGMENT": "judgment",
    "FINAL": "final",
    "FINAL_REASON": "finalReason",
}


class EventError(Exception):
    """사건을 지금 단계에 적용할 수 없다. 라우터가 400 INVALID_INPUT 으로 바꾼다."""


def _apply_inquiry(state: dict, field: str | None, value: Any) -> None:
    key = INQUIRY_FIELDS.get(field or "")
    if key is None:
        raise EventError("inquiry_field")
    q = dict(state.get("inquiry") or {})
    if key == "confirmed":
        q[key] = bool(value)
    elif key == "observed":
        if value not in catalog.CONDITIONS:
            raise EventError("inquiry_condition")
        q[key] = sorted({*(q.get("observed") or []), value})
    elif key == "judgment":
        if value not in catalog.JUDGMENTS:
            raise EventError("inquiry_judgment")
        q[key] = value
    else:
        q[key] = str(value or "")[:MAX_TEXT]
    state["inquiry"] = q


def _apply_theater(state: dict, step: int, event_type: str, value: Any) -> None:
    theater = dict(state.get("theater") or {})
    if event_type == "KEYWORD" and step == 0:
        theater["keyword"] = str(value or "")[:40]
    elif event_type == "APPROVE" and step == 1:
        theater["approved"] = True
    elif event_type == "EMOTION" and step == 3:
        if value not in catalog.EMOTIONS:
            raise EventError("emotion")
        theater["emotion"] = value
    elif event_type == "SCENE" and step == 2:
        direction = 1 if value in (1, "1", "NEXT") else -1
        if direction == 1 and theater.get("scene") == 1 and theater.get("choice") is None:
            raise EventError("choice_first")
        theater["scene"] = min(3, max(0, int(theater.get("scene", 0)) + direction))
    elif event_type == "CHOICE" and step == 2 and theater.get("scene") == 1:
        story = theater.get("story")
        if value not in (0, 1) or not story:
            raise EventError("choice")
        scenes = list(story["scenes"])
        scenes[2] = story["branches"][value]
        theater["story"] = {**story, "scenes": scenes}
        theater["choice"] = value
        theater["scene"] = 2
    else:
        raise EventError("event_step")
    state["theater"] = theater


def apply_event(activity: Activity, step: int, state: dict, event: dict) -> dict:
    """사건 하나를 초안에 반영해 새 state 를 돌려준다(프런트 `transition` 의 사건 처리와 같다)."""
    new = {**state, "lab": dict(state.get("lab") or {}), "theater": dict(state.get("theater") or {})}
    kind, field, value = event.get("type"), event.get("field"), event.get("value")
    if kind == "TEXT":
        new["text"] = str(value or "")[:MAX_TEXT]
    elif kind == "HINT":
        new["hints"] = int(new.get("hints", 0)) + 1
    elif kind == "TOPIC":
        if activity.track != "lab" or step != 0:
            raise EventError("event_step")
        new["lab"]["topic"] = str(value or "")[:60]
    elif kind == "LAB_VALUE":
        if activity.track != "lab" or step != 2:
            raise EventError("event_step")
        number = max(10, min(90, int(value)))
        new["lab"].update(
            {
                "value": number,
                "low": bool(new["lab"].get("low")) or number <= 30,
                "high": bool(new["lab"].get("high")) or number >= 70,
            }
        )
    elif kind == "OBSERVATION":
        key = OBSERVATION_FIELDS.get(field or "")
        if key is None:
            raise EventError("observation_field")
        if activity.id == INQUIRY_ACTIVITY:
            _apply_inquiry(new, "OBSERVED", "low" if key in ("low", "a") else "high")
        elif activity.track == "lab" and step == 2:
            new["lab"][key] = True if key in ("low", "high") else str(value or "")[:MAX_OBSERVATION]
        else:
            raise EventError("event_step")
    elif kind == "INQUIRY":
        if activity.id != INQUIRY_ACTIVITY:
            raise EventError("event_step")
        _apply_inquiry(new, field, value)
    elif kind == "RUN":
        if activity.id != PATH_ACTIVITY:
            raise EventError("event_step")
        path = dict(new.get("path") or {})
        path["runs"] = int(path.get("runs", 0)) + 1
        if field == "arrived":
            path["wins"] = int(path.get("wins", 0)) + 1
        path["awaitingChallenge"] = bool(value) if isinstance(value, bool) else False
        new["path"] = path
    elif kind in ("KEYWORD", "APPROVE", "SCENE", "CHOICE", "EMOTION"):
        if activity.track != "theater":
            raise EventError("event_step")
        _apply_theater(new, step, kind, value)
    else:
        raise EventError("event_type")
    return new


# --- 단계 이동 ------------------------------------------------------------------


def advance(activity: Activity, step: int, state: dict, minimum: int) -> tuple[int, dict]:
    """조건을 다시 검사하고 통과하면 다음 단계로. 프런트 `transition` 의 `advance` 갈래와 같다."""
    if step >= max_step(activity):
        raise StepError(["STEP_DONE"], minimum)
    codes = missing(activity, step, state, minimum)
    if codes:
        raise StepError(codes, minimum)
    new = {**state, "lab": dict(state.get("lab") or {}), "theater": dict(state.get("theater") or {})}
    text = str(new.get("text", "")).strip()
    if activity.track == "forest" and step >= 1:
        question = catalog.FORESTS[activity.id].questions[step - 1]
        new["answers"] = [*new.get("answers", []), {"question": question, "text": text}]
        new["followup"] = f"“{text[:100]}”라고 말했구나. 직접 본 것과 더 확인하고 싶은 것은 무엇일까?"
        new["text"] = ""
    if activity.track == "lab" and activity.id not in catalog.SIMULATED:
        if step == 0:
            lab = new["lab"]
            if blocked_keyword(lab.get("topic", "")):
                raise StepError(["BLOCKED_KEYWORD"], minimum)
            if lab.get("mode") == "custom" and re.search(r"그림자|빛", lab.get("topic", "")):
                lab["mode"], new["title"] = "shadow", "그림자는 왜 달라질까?"
            elif lab.get("mode") == "custom" and re.search(r"저울|무게", lab.get("topic", "")):
                lab["mode"], new["title"] = "balance", "저울은 언제 나란해질까?"
            elif lab.get("mode") == "custom":
                new["title"] = f"{lab.get('topic', '')} 관찰 노트"
        if writing_step(activity.track, activity.id, step):
            if step == 1:
                new["lab"]["prediction"] = text
            new["answers"] = [*new.get("answers", []), {"question": catalog.LAB_QUESTIONS[step], "text": text}]
            new["followup"] = f"“{text[:100]}”라고 생각했구나. 다른 조건에서도 같을지, 어떻게 확인할 수 있을까?"
            new["text"] = ""
    if activity.track == "theater":
        if step == 0:
            keyword = new["theater"].get("keyword", "")
            if blocked_keyword(keyword):
                raise StepError(["BLOCKED_KEYWORD"], minimum)
            new["theater"]["story"] = catalog.make_story(activity.id, keyword)
        if step == 3:
            new["answers"] = [*new.get("answers", []), {"question": catalog.THEATER_QUESTION, "text": text}]
            new["text"] = ""
    return step + 1, new


# --- 완료 기록 ------------------------------------------------------------------


def record_answers(activity: Activity, state: dict) -> list[dict]:
    """책장에 남길 질문·답 묶음. 시뮬레이터 활동은 기록된 값으로 만든다(프런트 `toRecord`)."""
    if activity.id == INQUIRY_ACTIVITY:
        q = state.get("inquiry") or {}
        observed = "\n".join(
            f"{catalog.CONDITIONS[c]['label']}: {catalog.CONDITIONS[c]['result']}" for c in q.get("observed") or []
        )
        judgment = catalog.JUDGMENTS.get(q.get("judgment") or "", "지금 내 생각")
        return [
            {"question": "처음 생각과 이유", "text": f"{q.get('initial', '')}\n이유: {q.get('reason', '')}"},
            {"question": "내가 확인한 뜻", "text": q.get("meaning", "")},
            {"question": "내가 살펴본 조건과 결과", "text": observed},
            {"question": judgment, "text": f"{q.get('final', '')}\n이유: {q.get('finalReason', '')}"},
        ]
    if activity.id == PATH_ACTIVITY:
        p = state.get("path") or {}
        return [
            {"question": "티키를 보낸 횟수", "text": f"{p.get('runs', 0)}번"},
            {"question": "우체국에 도착한 횟수", "text": f"{p.get('wins', 0)}번"},
        ]
    return list(state.get("answers") or [])


def thought_journey(activity: Activity, state: dict, answers: list[dict]) -> dict:
    """책장 기록의 생각 과정. 점수는 만들지 않는다."""
    texts = [a.get("text", "") for a in answers]
    theater = state.get("theater") or {}
    alternatives = [t for t in texts[1:-1] if t]
    if activity.track == "theater" and theater.get("story"):
        alternatives = [theater["story"]["scenes"][2]] if theater.get("choice") is not None else []
    return {
        "initialIdea": texts[0] if texts else "",
        "evidence": list(catalog.FORESTS[activity.id].evidence) if activity.id in catalog.FORESTS else [],
        "alternatives": alternatives,
        "finalReflection": texts[-1] if texts else "",
    }
