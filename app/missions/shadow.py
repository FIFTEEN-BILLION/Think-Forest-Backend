"""그림자 탐구 미션 — 모형·은행·판정 규칙의 단일 진실 원천.

점광원·평면·수직 막대기 모형: L = d × h / (H − h). 모든 이산 조합에서 H > h 이다.
LLM 은 여기 값을 바꾸지 못한다. 결과 계산·교란 검출·설득 판정은 전부 결정론이다.
효과(effect)는 항상 '더 높게/크게/멀게/밝게' 한 방향으로 정규화해서 비교한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product
from typing import Literal

Variable = Literal["lightHeight", "stickHeight", "distance", "brightness"]
Effect = Literal["longer", "shorter", "same"]
ClaimEffect = Literal["longer", "shorter", "same", "unknown"]
Missing = Literal["evidence", "fairness", "variable", "direction"]
Judgment = Literal["agree", "disagree", "unsure"]
Setup = dict[str, str]

VARIABLES: tuple[str, ...] = ("lightHeight", "stickHeight", "distance", "brightness")

# 값이 커지는 순서. 앞이 작고 뒤가 크다.
LEVELS: dict[str, tuple[str, ...]] = {
    "lightHeight": ("low", "mid", "high"),
    "stickHeight": ("short", "tall"),
    "distance": ("near", "far"),
    "brightness": ("dim", "bright"),
}
_VALUES: dict[str, dict[str, int]] = {
    "lightHeight": {"low": 4, "mid": 6, "high": 9},
    "stickHeight": {"short": 1, "tall": 2},
    "distance": {"near": 2, "far": 4},
}

LABELS: dict[str, dict] = {
    "lightHeight": {
        "name": "빛의 높이",
        "up": "빛을 높이면",
        "levels": {"low": "낮게", "mid": "가운데", "high": "높게"},
    },
    "stickHeight": {
        "name": "막대기 키",
        "up": "막대기를 크게 하면",
        "levels": {"short": "작게", "tall": "크게"},
    },
    "distance": {
        "name": "빛과 막대기 사이 거리",
        "up": "빛을 멀리 두면",
        "levels": {"near": "가깝게", "far": "멀게"},
    },
    "brightness": {
        "name": "빛의 밝기",
        "up": "빛을 밝게 하면",
        "levels": {"dim": "보통", "bright": "밝게"},
    },
}

BASE_SETUP: Setup = {
    "lightHeight": "mid",
    "stickHeight": "short",
    "distance": "near",
    "brightness": "dim",
}

# 모형 안에서의 실제 관계('더 높게/크게/멀게/밝게' 했을 때). 4단계 사실 문장의 근거.
TRUTH: dict[str, Effect] = {
    "lightHeight": "shorter",
    "stickHeight": "longer",
    "distance": "longer",
    "brightness": "same",
}

FACT_LINES: dict[str, str] = {
    "lightHeight": "빛이 높을수록 그림자는 짧아졌어요.",
    "stickHeight": "막대기가 클수록 그림자는 길어졌어요.",
    "distance": "빛이 멀수록 그림자는 길어졌어요.",
    "brightness": "빛의 밝기는 그림자 길이를 바꾸지 않았어요.",
}
MODEL_NOTE = "점 모양 빛과 곧게 선 막대기로 만든 모형이에요. 실제 햇빛이나 측정값과 다를 수 있어요."
PARENT_QUESTION = "손전등으로 인형 그림자를 만들어 볼까? 무엇을 바꾸면 그림자가 길어질지 먼저 예상해 보자."

_INVERT: dict[str, str] = {"longer": "shorter", "shorter": "longer", "same": "same", "unknown": "unknown"}
_EPS = 1e-9


# ---------------------------------------------------------------------------
# 모형 계산
# ---------------------------------------------------------------------------


def validate_setup(setup: dict) -> Setup:
    """변인 네 개가 모두 허용된 수준인지 확인한다. 아니면 ValueError."""
    clean: Setup = {}
    for var in VARIABLES:
        level = setup.get(var)
        if level not in LEVELS[var]:
            raise ValueError(f"{var} 수준이 올바르지 않음: {level!r}")
        clean[var] = level
    return clean


def shadow_length(setup: dict) -> float:
    s = validate_setup(setup)
    big_h = _VALUES["lightHeight"][s["lightHeight"]]
    h = _VALUES["stickHeight"][s["stickHeight"]]
    d = _VALUES["distance"][s["distance"]]
    return round(d * h / (big_h - h), 3)


def length_table() -> list[dict]:
    """24개 조합 전체의 그림자 길이. 프론트는 이 표로만 그림을 그린다."""
    rows = []
    for combo in product(*(LEVELS[v] for v in VARIABLES)):
        setup = dict(zip(VARIABLES, combo, strict=True))
        rows.append({"setup": setup, "length": shadow_length(setup)})
    return rows


@dataclass(frozen=True)
class Experiment:
    base: Setup
    compare: Setup
    base_length: float
    compare_length: float
    changed: tuple[str, ...]
    effect: Effect  # compare 가 base 보다 어떻게 됐는지

    @property
    def fair(self) -> bool:
        return len(self.changed) == 1

    @property
    def variable(self) -> str | None:
        return self.changed[0] if self.fair else None

    @property
    def normalized_effect(self) -> Effect | None:
        """공정 실험일 때 '그 변인을 키웠을 때'의 효과. 불공정이면 None."""
        if not self.fair:
            return None
        var = self.changed[0]
        order = LEVELS[var]
        increased = order.index(self.compare[var]) > order.index(self.base[var])
        return self.effect if increased else _INVERT[self.effect]  # type: ignore[return-value]


def run_experiment(base: dict, compare: dict) -> Experiment:
    b, c = validate_setup(base), validate_setup(compare)
    lb, lc = shadow_length(b), shadow_length(c)
    effect: Effect = "same" if abs(lc - lb) < _EPS else ("longer" if lc > lb else "shorter")
    changed = tuple(v for v in VARIABLES if b[v] != c[v])
    return Experiment(b, c, lb, lc, changed, effect)


# 실험 설계 도움 사다리. 1회 탐침 → 2회 초점 힌트 → 3회 명시적 설명(검수 문구).
DESIGN_FEEDBACK: dict[str, str] = {
    "none": "아무것도 바꾸지 않으면 두 장면이 똑같아. 하나를 골라 바꿔 볼까?",
    "probe": "두 가지를 한꺼번에 바꿨네. 그림자가 달라졌다면, 무엇 때문인지 알 수 있을까?",
    "hint": "하나만 바꾸고 나머지는 그대로 두면, 무엇 때문에 달라졌는지 알 수 있어.",
    "explanation": (
        "공정한 비교는 한 번에 하나만 바꾸는 거야. 예를 들어 밝기만 바꾸고 "
        "빛 높이·막대기·거리는 그대로 두면, 밝기 때문인지 알 수 있어."
    ),
}


# ---------------------------------------------------------------------------
# 친구 생각 은행 · 주장
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FriendBelief:
    id: str
    line: str
    variable: str
    claimed_effect: Effect
    source_note: str
    review_status: str = "needs_review"


FRIEND_BELIEFS: tuple[FriendBelief, ...] = (
    FriendBelief(
        "brightness_longer",
        "나는 빛이 밝으면 그림자가 길어진다고 생각해!",
        "brightness",
        "longer",
        "교사용 자료(NSTA)의 오개념 목록. 논문 근거 없음 — 검수 필요",
    ),
    FriendBelief(
        "light_higher_longer",
        "나는 빛을 높이면 그림자도 길어진다고 생각해!",
        "lightHeight",
        "longer",
        "방향 반대 오개념 — 검수 필요",
    ),
    FriendBelief(
        "light_irrelevant",
        "그림자는 막대기 거라서, 빛이 달라져도 그대로라고 생각해!",
        "lightHeight",
        "same",
        "Feher & Rice(1988) 계열: 그림자를 물체의 속성으로 봄 — 검수 필요",
    ),
    FriendBelief(
        "distance_irrelevant",
        "빛이 멀리 가도 그림자는 똑같을 거라고 생각해!",
        "distance",
        "same",
        "빛–물체 거리 관계를 모르는 경향(2007 연구) — 검수 필요",
    ),
)
BELIEF_IDS: tuple[str, ...] = tuple(b.id for b in FRIEND_BELIEFS)


def get_belief(belief_id: str) -> FriendBelief | None:
    return next((b for b in FRIEND_BELIEFS if b.id == belief_id), None)


@dataclass(frozen=True)
class Claim:
    variable: str
    effect: ClaimEffect


def belief_differs(belief: FriendBelief, claims: list[Claim]) -> bool:
    """친구 생각이 아이가 확인한 주장과 같으면 안 된다(대비가 사라짐)."""
    return not any(c.variable == belief.variable and c.effect == belief.claimed_effect for c in claims)


_PREFERRED_BELIEF: dict[str, str] = {
    "shorter": "brightness_longer",
    "longer": "light_irrelevant",
    "same": "light_higher_longer",
    "unknown": "light_higher_longer",
}


def fallback_belief(prediction: str, claims: list[Claim]) -> FriendBelief:
    preferred = _PREFERRED_BELIEF.get(prediction, "light_higher_longer")
    order = [preferred, *(i for i in BELIEF_IDS if i != preferred)]
    for belief_id in order:
        belief = get_belief(belief_id)
        if belief and belief_differs(belief, claims):
            return belief
    return get_belief(preferred)  # type: ignore[return-value]


def choose_belief(candidate_id: str | None, prediction: str, claims: list[Claim]) -> tuple[FriendBelief, bool]:
    """LLM 후보를 검증한다. (선택된 생각, 후보를 그대로 썼는지)."""
    candidate = get_belief(candidate_id) if candidate_id else None
    if candidate and belief_differs(candidate, claims):
        return candidate, True
    return fallback_belief(prediction, claims), False


# ---------------------------------------------------------------------------
# 규칙 기반 주장 파서 — LLM 실패 시 폴백(정직하게 ai=false)
# ---------------------------------------------------------------------------

_VAR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("brightness", re.compile(r"밝|어둡|환하|세게|강하|빛이\s*세")),
    ("distance", re.compile(r"멀|가까|거리|떨어")),
    ("stickHeight", re.compile(r"막대\S*\s*(키|크|커|작)|키가\s*(크|커|작)")),
    ("lightHeight", re.compile(r"높|낮|올리|올려|내리|내려|위로|아래로")),
)
_DOWN = re.compile(r"낮|내리|내려|아래|작|가까|어둡")
_SAME = re.compile(r"같|그대로|똑같|안\s*변|안\s*바뀌|상관\s*없|관계\s*없|변하지\s*않|달라지지\s*않")


def _effect_word(text: str) -> ClaimEffect:
    t = text.replace("길이", "")
    longer, shorter, same = "길" in t, "짧" in t, bool(_SAME.search(t))
    if same and not (longer or shorter):
        return "same"
    if longer and not shorter:
        return "longer"
    if shorter and not longer:
        return "shorter"
    return "unknown"


def parse_claims(text: str) -> list[Claim]:
    """아이 문장에서 (변인, 효과)를 대략 뽑는다. 방향은 '키웠을 때'로 정규화."""
    if not text:
        return []
    effect = _effect_word(text)
    claims: list[Claim] = []
    stick_found = False
    for var, pattern in _VAR_PATTERNS:
        if not pattern.search(text):
            continue
        if var == "stickHeight":
            stick_found = True
        if var == "lightHeight" and stick_found and not re.search(r"빛|해|전등|불", text):
            continue
        normalized = _INVERT[effect] if (_DOWN.search(text) and var != "brightness") else effect
        claims.append(Claim(var, normalized))  # type: ignore[arg-type]
    return claims


# ---------------------------------------------------------------------------
# 친구 가르치기 — 설득 판정(결정론)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeachVerdict:
    convinced: bool
    missing: Missing | None
    card: Experiment | None


def judge_teaching(
    belief: FriendBelief, cards: list[Experiment], claim: Claim | None, uses_evidence: bool
) -> TeachVerdict:
    """증거 없는 주장·불공정 증거·반대 방향은 절대 설득 성공이 아니다."""
    if not cards:
        return TeachVerdict(False, "evidence", None)
    relevant = [c for c in cards if c.fair and c.variable == belief.variable]
    if not relevant:
        touches = any(belief.variable in c.changed for c in cards)
        return TeachVerdict(False, "fairness" if touches else "variable", None)
    card = relevant[0]
    if claim is None or claim.variable != belief.variable:
        return TeachVerdict(False, "variable", card)
    if claim.effect != card.normalized_effect:
        return TeachVerdict(False, "direction", card)
    if not uses_evidence:
        return TeachVerdict(False, "evidence", card)
    return TeachVerdict(True, None, card)


_EFFECT_PHRASE: dict[str, str] = {
    "longer": "길어진다는",
    "shorter": "짧아진다는",
    "same": "달라지지 않는다는",
}

TEACH_PROBES: dict[str, str] = {
    "evidence": "정말? 어떤 실험에서 그걸 봤어? 실험 카드를 보여 줄래?",
    "fairness": "그 실험은 여러 개를 같이 바꿨잖아. {name}만 바꾼 실험도 있어?",
    "variable": "나는 {name} 이야기를 했는데, 그거랑 어떤 상관이 있어?",
    "direction": "{up} 그림자가 어떻게 됐는지 카드에서 다시 볼래?",
}
TEACH_HINT = "{name}만 바꾼 카드를 골라서, 그림자 길이가 어떻게 달라졌는지 말해 줘."
TEACH_EXPLANATION = (
    "친구를 설득하려면 증거가 필요해. {name}만 바꾼 공정한 실험 카드를 고르고, "
    "'{up} 그림자가 이렇게 됐어'처럼 본 것을 그대로 말해 주면 돼."
)
CONVINCED_LINE = "와, {up} 그림자가 {phrase} 걸 네 실험으로 봤구나. 내 생각을 바꿀게!"


def belief_words(belief: FriendBelief) -> dict[str, str]:
    label = LABELS[belief.variable]
    return {"name": label["name"], "up": label["up"]}


def convinced_line(belief: FriendBelief) -> str:
    return CONVINCED_LINE.format(phrase=_EFFECT_PHRASE[TRUTH[belief.variable]], **belief_words(belief))


# 친구 대사가 가르침 전에 정답 규칙을 말하면 안 된다.
_TRUTH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(높|올리|올려|위로).{0,12}짧"),
    re.compile(r"(낮|내리|내려|아래로).{0,12}길"),
    re.compile(r"밝.{0,12}(상관\s*없|관계\s*없|그대로|안\s*변|달라지지\s*않|바꾸지\s*않|영향.{0,3}없|똑같)"),
    re.compile(r"(멀|거리).{0,12}길"),
    re.compile(r"(막대|키).{0,12}(크|커|클).{0,12}길"),
)


def leaks_answer(text: str) -> bool:
    return any(p.search(text or "") for p in _TRUTH_PATTERNS)


PREDICTION_PHRASE: dict[str, str] = {"longer": "길어질", "shorter": "짧아질", "same": "그대로일"}


def fallback_restatement(prediction: str) -> str:
    if prediction not in PREDICTION_PHRASE:
        return "빛을 높이면 그림자가 어떻게 될지 아직 잘 모르겠구나."
    return f"빛을 높이면 그림자가 {PREDICTION_PHRASE[prediction]} 거라고 생각했구나."


# ---------------------------------------------------------------------------
# 새 상황 도전 은행
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Challenge:
    id: str
    line: str
    compare_change: dict
    friend_prediction: Effect
    target: str  # 변인 이름 또는 "confound"

    @property
    def confounded(self) -> bool:
        return self.target == "confound"

    @property
    def base(self) -> Setup:
        return dict(BASE_SETUP)

    @property
    def compare(self) -> Setup:
        return {**BASE_SETUP, **self.compare_change}

    @property
    def experiment(self) -> Experiment:
        return run_experiment(self.base, self.compare)

    @property
    def friend_correct(self) -> bool:
        return not self.confounded and self.experiment.effect == self.friend_prediction


CHALLENGES: tuple[Challenge, ...] = (
    Challenge(
        "tall_stick", "막대기만 크게 바꾸면, 그림자는 그대로일 거야.", {"stickHeight": "tall"}, "same", "stickHeight"
    ),
    Challenge(
        "far_light", "빛만 막대기에서 멀리 두면, 그림자가 짧아질 거야.", {"distance": "far"}, "shorter", "distance"
    ),
    Challenge(
        "low_light_correct", "빛만 낮게 내리면, 그림자가 길어질 거야.", {"lightHeight": "low"}, "longer", "lightHeight"
    ),
    Challenge(
        "bright_same_correct",
        "빛만 밝게 하면, 그림자 길이는 그대로일 거야.",
        {"brightness": "bright"},
        "same",
        "brightness",
    ),
    Challenge(
        "confounded_claim",
        "빛을 높이면서 밝게도 했더니 그림자가 짧아졌어. 그러니까 밝아서 짧아진 거야!",
        {"lightHeight": "high", "brightness": "bright"},
        "shorter",
        "confound",
    ),
)
CHALLENGE_IDS: tuple[str, ...] = tuple(c.id for c in CHALLENGES)


def get_challenge(challenge_id: str) -> Challenge | None:
    return next((c for c in CHALLENGES if c.id == challenge_id), None)


def fallback_challenge(experiments: list[Experiment], convinced: bool) -> Challenge:
    if convinced:
        return get_challenge("confounded_claim")  # type: ignore[return-value]
    tested = {e.variable for e in experiments if e.fair}
    for challenge_id in ("tall_stick", "far_light"):
        challenge = get_challenge(challenge_id)
        if challenge and challenge.target not in tested:
            return challenge
    return get_challenge("low_light_correct")  # type: ignore[return-value]


def judgment_correct(challenge: Challenge, judgment: str) -> bool:
    if challenge.confounded:
        return judgment in ("disagree", "unsure")
    return judgment == ("agree" if challenge.friend_correct else "disagree")


# ---------------------------------------------------------------------------
# 공개 페이로드
# ---------------------------------------------------------------------------


def mission_payload() -> dict:
    """GET /missions/shadow 응답 본문(정답 문장은 4단계 표시용)."""
    return {
        "id": "shadow",
        "question": "빛을 높이면 그림자는 어떻게 될까?",
        "variables": [
            {"id": v, "name": LABELS[v]["name"], "up": LABELS[v]["up"], "levels": [
                {"id": level, "label": LABELS[v]["levels"][level]} for level in LEVELS[v]
            ]}
            for v in VARIABLES
        ],
        "baseSetup": dict(BASE_SETUP),
        "table": length_table(),
        "designFeedback": dict(DESIGN_FEEDBACK),
        "friendBeliefs": [
            {"id": b.id, "line": b.line, "variable": b.variable, "claimedEffect": b.claimed_effect}
            for b in FRIEND_BELIEFS
        ],
        "truth": dict(TRUTH),
        "facts": dict(FACT_LINES),
        "modelNote": MODEL_NOTE,
        "parentQuestion": PARENT_QUESTION,
    }
