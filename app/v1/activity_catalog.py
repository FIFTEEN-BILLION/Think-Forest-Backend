"""생각 모험 활동 카탈로그 — 서버의 단일 원천.

프런트 `features/village/data/catalog.ts`(PLACES·CATALOG·FORESTS·EMOTIONS)와 `lib/learning.ts` 의 `makeStory`,
`lib/inquiry.ts` 의 CONDITIONS·JUDGMENTS 를 그대로 옮겨 왔다. 값이 갈라지면 서버가 기준이다.
콘텐츠만 둔다. 단계 조건 판정은 `activity_rules.py` 가 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 공백을 뺀 최소 글자 수. 프런트 gateSize(쉬움 8 · 보통 15 · 도전 25)를 활동 난이도로 고정한다.
MIN_BY_LEVEL: dict[str, int] = {"쉬움": 8, "보통": 15, "도전": 25}

PLACES: dict[str, dict] = {
    "forest": {
        "name": "우리 아이 생각친구, 티키",
        "area": "사고력",
        "color": "violet",
        "icon": "tree",
        "line": "단서를 살펴보고, 내 생각의 이유를 찾아요.",
        "description": "이야기 속 작은 단서를 모아 나만의 생각을 꺼내는 곳",
        "steps": ["이야기 만나기", "내 생각 꺼내기", "새 단서 살펴보기", "생각 정리하기", "모험 돌아보기"],
    },
    "lab": {
        "name": "호기심 실험실",
        "area": "과학 · 수학 · 역사",
        "color": "teal",
        "icon": "flask",
        "line": "직접 바꾸고 관찰하며, 궁금함을 발견해요.",
        "description": "궁금한 것을 예상하고 직접 바꾸며 발견하는 곳",
        "steps": ["활동 준비하기", "먼저 예상하기", "직접 관찰하기", "발견 설명하기", "다시 생각하기", "발견 돌아보기"],
    },
    "theater": {
        "name": "마음극장",
        "area": "인성 · 애니메이션",
        "color": "coral",
        "icon": "theater",
        "line": "이야기 속 친구가 되어, 서로의 마음을 읽어요.",
        "description": "친구의 마음을 만나고 내가 건넬 말을 생각하는 곳",
        "steps": ["이야기 준비하기", "보호자 미리보기", "이야기와 선택", "내 마음 표현하기", "마음 돌아보기"],
    },
}
TRACKS: tuple[str, ...] = ("forest", "lab", "theater")
# 활동 기록을 책장에 남길 때 쓰는 v1 주제 분류.
CATEGORY_OF_TRACK: dict[str, str] = {"forest": "THINKING", "lab": "SCIENCE", "theater": "FEELINGS"}

PATH_ACTIVITY = "path-teaching"
INQUIRY_ACTIVITY = "first-inquiry"
# 두 활동은 프런트 시뮬레이터가 단계를 끌고 간다. 서버는 기록된 사건만 보고 조건을 다시 센다.
SIMULATED = (PATH_ACTIVITY, INQUIRY_ACTIVITY)

EMOTIONS: tuple[str, ...] = ("걱정됐어요", "답답했어요", "궁금했어요", "안심됐어요", "뿌듯했어요", "잘 모르겠어요")
CONDITIONS: dict[str, dict[str, str]] = {
    "low": {"label": "빛을 낮게", "result": "그림자가 길게 뻗었어요.", "detail": "빛을 낮게 두었을 때"},
    "high": {"label": "빛을 높게", "result": "그림자가 짧아졌어요.", "detail": "빛을 높게 두었을 때"},
}
JUDGMENTS: dict[str, str] = {
    "keep": "처음 생각과 같아요",
    "change": "생각이 달라졌어요",
    "explore": "더 알아보고 싶어요",
}
INQUIRY_STEPS: tuple[str, ...] = ("처음 생각", "뜻 확인", "조건 바꾸기", "다시 생각", "처음과 지금")
PATH_STEPS: tuple[str, ...] = ("티키 가르치기", "배달 돌아보기")


@dataclass(frozen=True)
class Activity:
    id: str
    track: str
    title: str
    subtitle: str
    duration: int
    level: str
    tags: tuple[str, ...]
    description: str

    @property
    def place(self) -> dict:
        return PLACES[self.track]

    @property
    def area(self) -> str:
        return str(self.place["area"])

    @property
    def min_characters(self) -> int:
        return MIN_BY_LEVEL[self.level]

    @property
    def steps(self) -> tuple[str, ...]:
        if self.id == PATH_ACTIVITY:
            return PATH_STEPS
        if self.id == INQUIRY_ACTIVITY:
            return INQUIRY_STEPS
        return tuple(self.place["steps"])


CATALOG: tuple[Activity, ...] = (
    Activity(
        id=PATH_ACTIVITY,
        track="lab",
        title="티키에게 길 찾는 법 가르치기",
        subtitle="내 규칙대로 움직이는 티키, 우체국까지 갈 수 있을까?",
        duration=15,
        level="쉬움",
        tags=("티키 가르치기", "규칙", "시험해 보기"),
        description="카드로 티키에게 규칙을 가르치고, 티키가 멈춘 까닭을 찾아 규칙을 고쳐요. 마지막엔 새 지도에서 혼자 해결해요.",  # noqa: E501
    ),
    Activity(
        id=INQUIRY_ACTIVITY,
        track="lab",
        title="빛과 그림자, 나의 첫 탐구",
        subtitle="빛을 높이면 그림자는 어떻게 될까?",
        duration=10,
        level="쉬움",
        tags=("첫 탐구", "빛", "생각 비교"),
        description="내 생각을 먼저 쓰고, 두 가지 조건을 살펴본 뒤 처음과 지금의 생각을 나란히 보아요.",
    ),
    Activity(
        id="honey",
        track="forest",
        title="사라진 꿀단지의 단서",
        subtitle="정말 곰이 가져갔을까?",
        duration=12,
        level="보통",
        tags=("동물", "단서 찾기"),
        description="토끼와 함께 보이는 사실과 아직 모르는 것을 나누어 봐요.",
    ),
    Activity(
        id="seed",
        track="forest",
        title="싹이 나지 않은 화분",
        subtitle="같은 날 심었는데 왜 다를까?",
        duration=12,
        level="도전",
        tags=("식물", "다르게 생각하기"),
        description="두 화분을 비교하며 무엇을 확인해야 하는지 생각해요.",
    ),
    Activity(
        id="umbrella",
        track="forest",
        title="비가 오지 않은 날의 우산",
        subtitle="젖었다고 모두 비 때문일까?",
        duration=10,
        level="쉬움",
        tags=("일상", "사실과 추측"),
        description="하나의 단서로 여러 가지 가능성을 열어 두는 연습을 해요.",
    ),
    Activity(
        id="shadow",
        track="lab",
        title="그림자는 왜 달라질까?",
        subtitle="빛의 높이와 그림자의 길이",
        duration=15,
        level="보통",
        tags=("과학", "빛"),
        description="먼저 예상하고 빛의 높이를 움직이며 두 조건을 비교해요.",
    ),
    Activity(
        id="balance",
        track="lab",
        title="저울은 언제 나란해질까?",
        subtitle="가벼움과 무거움의 발견",
        duration=15,
        level="보통",
        tags=("수학", "무게"),
        description="왼쪽 추의 무게를 바꾸며 기울기의 변화를 관찰해요.",
    ),
    Activity(
        id="custom",
        track="lab",
        title="내가 정하는 궁금한 실험",
        subtitle="과학부터 옛날 사람들의 생활까지",
        duration=20,
        level="도전",
        tags=("자유 주제", "관찰 노트"),
        description="직접 확인한 자료와 두 가지 관찰을 나의 활동지에 모아요.",
    ),
    Activity(
        id="kindness",
        track="theater",
        title="함께 만드는 작은 다리",
        subtitle="서로 다른 생각을 이어 주는 배려",
        duration=12,
        level="보통",
        tags=("배려", "친구"),
        description="토끼와 곰이 되어 서로의 말을 듣는 방법을 골라요.",
    ),
    Activity(
        id="courage",
        track="theater",
        title="처음 무대에 서는 날",
        subtitle="작은 용기를 건네는 한마디",
        duration=12,
        level="쉬움",
        tags=("용기", "응원"),
        description="두근거리는 친구에게 내가 해 줄 수 있는 말을 생각해요.",
    ),
    Activity(
        id="waiting",
        track="theater",
        title="천천히 완성하는 우리 그림",
        subtitle="기다리는 동안 알게 된 마음",
        duration=12,
        level="보통",
        tags=("기다림", "협동"),
        description="서로 다른 속도를 가진 친구들과 함께 그림을 완성해요.",
    ),
)
BY_ID: dict[str, Activity] = {a.id: a for a in CATALOG}


@dataclass(frozen=True)
class Forest:
    intro: str
    clue: str
    questions: tuple[str, ...]
    evidence: tuple[str, ...] = field(default=())


FORESTS: dict[str, Forest] = {
    "honey": Forest(
        intro="토끼가 소풍 자리에 돌아왔어요. 식탁 위에 있던 꿀단지가 보이지 않아요. 곰은 나무 옆에 서 있고, 식탁 아래에는 작은 발자국이 있어요.",  # noqa: E501
        clue="다람쥐가 “나는 빈 바구니만 옮겼어”라고 말했어요. 아직 꿀단지를 옮기는 모습을 본 친구는 없어요. 곰이 근처에 있었다는 것만으로 알 수 있을까요?",  # noqa: E501
        questions=(
            "어떤 단서가 눈에 들어왔나요? 그 단서로 무엇을 생각했나요?",
            "새로운 말을 듣고 생각이 달라졌나요? 더 확인하고 싶은 것을 적어 보세요.",
            "아직 모르는 것은 무엇인가요? 다음에 어떻게 알아볼지 내 문장으로 남겨요.",
        ),
        evidence=("식탁 위에 꿀단지가 보이지 않아요.", "곰은 나무 옆에 서 있어요.", "식탁 아래에 작은 발자국이 있어요."),  # noqa: E501
    ),
    "seed": Forest(
        intro="같은 날 씨앗을 심었는데 창가 화분에서만 싹이 났어요. 문 옆 화분의 흙은 말라 있어요. 두 화분에 물을 준 양은 기록되어 있지 않아요.",  # noqa: E501
        clue="창가 화분과 문 옆 화분에는 서로 다른 씨앗이 심어져 있었대요. 빛만 비교해도 괜찮을까요?",
        questions=(
            "두 화분에서 다른 점을 찾아볼까요? 왜 눈에 들어왔나요?",
            "처음 생각을 확인하려면 무엇을 같게 해야 할까요?",
            "아직 확실하지 않은 점과 다음에 해 보고 싶은 관찰을 적어 보세요.",
        ),
        evidence=("같은 날 씨앗을 심었어요.", "문 옆 화분의 흙은 말라 있어요.", "물을 준 양은 기록되어 있지 않아요."),
    ),
    "umbrella": Forest(
        intro="맑은 날 아침, 여우가 젖은 우산을 들고 왔어요. 토끼는 “여우네 마을에는 비가 왔나 봐!”라고 말했어요. 우산 끝에서는 물방울이 떨어지고 있었어요.",  # noqa: E501
        clue="여우의 집 앞에는 물을 뿜는 분수가 있어요. 여우는 우산이 어디서 젖었는지 아직 말하지 않았어요.",
        questions=(
            "직접 알 수 있는 것과 추측을 나누어 적어 볼까요?",
            "비 말고도 우산이 젖을 수 있는 이유가 있을까요?",
            "어떤 질문을 하면 확실하게 알 수 있을까요?",
        ),
        evidence=("지금 이곳의 하늘은 맑아요.", "여우의 우산은 젖어 있어요.", "우산이 젖은 곳은 아직 몰라요."),
    ),
}

# 실험실 단계별 글쓰기 질문(프런트 transition 과 같은 문장).
LAB_QUESTIONS: dict[int, str] = {
    1: "관찰하기 전, 내 예상",
    3: "직접 관찰해서 발견한 것",
    4: "다른 조건에서도 그럴까? 더 확인하고 싶은 것",
}
THEATER_QUESTION = "친구에게 건넬 말과 그 이유"


def make_story(activity_id: str, keyword: str) -> dict:
    """프런트 `makeStory` 를 그대로 옮긴 대본. 두 갈래(branches) 중 고른 쪽이 3번째 장면이 된다."""
    if activity_id == "courage":
        return {
            "title": "처음 무대에 서는 날",
            "keyword": keyword,
            "scenes": [
                f"“{keyword}”를 생각하는 날, 토끼와 곰은 작은 음악회를 준비했어요.",
                "토끼가 “틀리면 어떡하지?”라며 무대 뒤에서 망설였어요. 곰도 처음에는 두근거렸대요.",
                "두 친구는 무대에 오르기 전에 서로의 마음을 나누기로 했어요.",
                "토끼는 준비한 만큼 천천히 노래했어요. 곰은 옆에서 박자를 맞춰 주었어요. 작은 한 걸음도 소중한 용기였어요.",  # noqa: E501
            ],
            "branches": [
                "곰이 “어떤 부분이 걱정돼?”라고 물었어요. 토끼는 첫 소절을 같이 연습해 달라고 부탁했어요.",
                "곰이 “나도 처음에는 떨렸어”라고 말했어요. 토끼는 혼자만 그런 게 아니라는 걸 알고 숨을 천천히 쉬었어요.",  # noqa: E501
            ],
        }
    if activity_id == "waiting":
        return {
            "title": "천천히 완성하는 우리 그림",
            "keyword": keyword,
            "scenes": [
                f"“{keyword}”를 생각하며 토끼와 곰이 커다란 그림을 그려요.",
                "토끼는 벌써 색칠을 마쳤지만 곰은 아직 선을 그리고 있어요. 토끼는 빨리 전시하고 싶었어요.",
                "두 친구는 원하는 것이 무엇인지 말해 보기로 했어요.",
                "토끼와 곰은 서로의 속도를 맞추어 그림을 완성했어요. 함께 기다린 시간도 그림의 한 부분이 되었어요.",
            ],
            "branches": [
                "토끼가 “어떤 부분을 더 그리고 싶어?”라고 물었어요. 곰은 좋아하는 나무를 자세히 그리고 싶다고 말했어요.",  # noqa: E501
                "토끼가 “나는 우리 그림을 빨리 보여 주고 싶어”라고 말했어요. 곰은 남은 부분을 함께 그려 보자고 했어요.",
            ],
        }
    return {
        "title": "함께 만드는 작은 다리",
        "keyword": keyword,
        "scenes": [
            f"“{keyword}”를 생각하는 날, 토끼와 곰은 시냇가에 작은 다리를 만들기로 했어요.",
            "토끼는 빨리 건너고 싶었어요. 곰은 다리가 튼튼한지 더 살펴보고 싶었지요. “우리 생각이 다른 것 같아.”",
            "두 친구는 잠시 멈추고 서로의 생각을 나누기로 했어요.",
            "토끼와 곰은 함께 살펴보고 다리를 고쳤어요. 서로의 마음을 물어보니 혼자서는 떠올리지 못한 생각이 생겼어요.",
        ],
        "branches": [
            "토끼가 “무엇이 걱정돼?”라고 물었어요. 곰은 흔들리는 나무판을 가리켰어요. 토끼는 고개를 끄덕였어요.",
            "토끼가 “나는 빨리 건너고 싶었어”라고 말했어요. 곰은 마음을 이해한 뒤, 흔들리는 나무판을 함께 살펴보자고 했어요.",  # noqa: E501
        ],
    }


def visuals(activity: Activity) -> dict:
    """활동 화면에 필요한 시각 자료. 프런트가 아이콘·색과 함께 보여 준다."""
    place = activity.place
    if activity.track == "forest":
        forest = FORESTS[activity.id]
        return {"kind": "EVIDENCE", "icon": place["icon"], "color": place["color"], "items": list(forest.evidence)}
    if activity.track == "theater":
        return {"kind": "EMOTIONS", "icon": place["icon"], "color": place["color"], "items": list(EMOTIONS)}
    if activity.id == INQUIRY_ACTIVITY:
        items = [f"{c['label']} — {c['result']}" for c in CONDITIONS.values()]
        return {"kind": "CONDITIONS", "icon": place["icon"], "color": place["color"], "items": items}
    if activity.id == PATH_ACTIVITY:
        return {"kind": "MAP", "icon": place["icon"], "color": place["color"], "items": ["편지", "시냇물", "우체국"]}
    label = "빛의 높이" if activity.id == "shadow" else "왼쪽 추의 무게" if activity.id == "balance" else "내가 정한 조건"  # noqa: E501
    return {"kind": "CONTROL", "icon": place["icon"], "color": place["color"], "items": [label, "10 ~ 90"]}


def intro(activity: Activity) -> str:
    forest = FORESTS.get(activity.id)
    return forest.intro if forest else f"{activity.subtitle} {activity.description}"


def clue(activity: Activity) -> str | None:
    forest = FORESTS.get(activity.id)
    return forest.clue if forest else None


def questions(activity: Activity) -> list[str]:
    forest = FORESTS.get(activity.id)
    if forest:
        return list(forest.questions)
    if activity.id == INQUIRY_ACTIVITY:
        return ["지금 내 생각과 그 이유는 뭐야?", "내가 말하려던 뜻이 이게 맞아?", "두 조건을 살펴보니 어땠어?"]
    if activity.id == PATH_ACTIVITY:
        return ["티키에게 어떤 길을 알려 줄까?", "티키가 멈춘 까닭은 뭘까?"]
    if activity.track == "lab":
        return list(LAB_QUESTIONS.values())
    return [THEATER_QUESTION]
