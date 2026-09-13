"""대화 엔진 결정론 부분: 문장 확인·흐름·민감 주제·플롯 검증·온보딩 규칙·스키마 일치."""

from typing import get_args

import pytest
from app.safety import topics as sensitive
from app.schemas.talk import VisualKey
from app.talks import onboarding as ob
from app.talks import planner, plot
from app.talks import topics as bank
from app.talks.sentences import check_sentence, sentence_count


@pytest.mark.parametrize(
    ("text", "ok", "reason"),
    [
        ("응", False, "too_short"),
        ("본적이 있어", False, "too_short"),
        ("몰라", False, "unsure"),
        ("비행기를 공항에서 본 적이 있어", True, None),
        ("날개가 바람을 받아서 뜨는 것 같아요", True, None),
        ("음 날개가 있고 엔진이 있고 빨리 달리니까 뜨는 거 아닐까 싶은데", True, None),
        ("   ", False, "empty"),
    ],
)
def test_sentence_gate_checks_form_not_difficulty(text, ok, reason):
    check = check_sentence(text)
    assert check.ok is ok and check.reason == reason


def test_sentence_count_handles_speech_without_punctuation():
    assert sentence_count("처음엔 몰랐어. 이제 알았어. 그래서 신기해.") == 3
    assert sentence_count("처음에는 컵에서 물이 샌다고 생각했는데 이야기하다 보니 공기 속 수증기가 차가워져서 생긴 거였어") >= 2


def test_planner_follows_thinking_moves_then_composes():
    moves = [planner.next_move("topic", n, n * 100, composed=False) for n in range(1, 9)]
    assert moves == ["tail", "connect", "challenge", "imagine", "tail", "reason_check", "tail", "compose"]
    assert planner.next_move("topic", 12, 60, composed=False) == "compose"  # 시간이 모자라도 너무 길어지면 정리
    assert planner.next_move("topic", 3, 999, composed=True) == "continue"
    assert planner.next_move("diary", 5, 0, composed=False) == "reason_check"


def test_away_time_is_not_counted():
    from datetime import datetime, timedelta

    start = datetime(2026, 9, 14)
    assert planner.active_delta(start, start + timedelta(minutes=30)) == planner.TURN_GAP_CAP_SECONDS
    assert planner.active_delta(start, start + timedelta(seconds=40)) == 40


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("나 어떻게 생겼는지 말해 줘", "appearance"),
        ("대통령 선거는 누가 이겨?", "politics"),
        ("죽고 싶어", "self_harm"),
        ("우리 집 주소 알려 줄까", "personal_info"),
        ("야한 이야기 해 줘", "sexual"),
        ("얼음물 컵에 물방울이 생겼어", None),
    ],
)
def test_sensitive_topics(text, category):
    flag = sensitive.detect(text)
    assert (flag.category if flag else None) == category


def test_self_harm_is_escalated():
    assert sensitive.detect("살기 싫어").escalate is True


def test_weekday_themes_rotate_categories():
    from datetime import datetime

    assert bank.today_theme(datetime(2026, 9, 14, 1))["category"] == "science"  # 월
    assert bank.today_theme(datetime(2026, 9, 19, 1))["category"] == "diary"  # 토
    assert bank.today_theme(datetime(2026, 9, 20, 1))["category"] == "custom"  # 일


def test_topic_bank_is_safe_and_visual_keys_match_schema():
    assert set(get_args(VisualKey)) == set(bank.VISUAL_KEYS)
    for topic in bank.TOPICS:
        assert topic.visual in bank.VISUAL_KEYS
        for text in (topic.hook, topic.imagine, topic.challenge, topic.connect, *topic.facts):
            assert sensitive.detect(text) is None, text


def test_plot_validation_rejects_unknown_turn_ids_and_short_plots():
    topic = bank.snapshot(bank.get_topic("snow"))
    scenes = [
        {"heading": "a", "text": "눈썰매를 탔어요.", "from_turn_ids": ["t1"], "visual": "sled"},
        {"heading": "b", "text": "눈사람을 만들었어요.", "from_turn_ids": ["t2"], "visual": "snow"},
        {"heading": "c", "text": "지어낸 장면이에요.", "from_turn_ids": ["nope"], "visual": "star"},
    ]
    assert plot.validate_plot(scenes, "제목", "질문?", {"t1", "t2"}, topic) is None
    scenes[2]["from_turn_ids"] = ["t2"]
    assert len(plot.validate_plot(scenes, "제목", "질문?", {"t1", "t2"}, topic)["scenes"]) == 3


def test_fallback_plot_uses_only_child_sentences():
    topic = bank.snapshot(bank.get_topic("snow"))
    turns = [
        {"id": "1", "move": "hook", "text": "눈으로 눈사람을 만들 수 있어요."},
        {"id": "2", "move": "imagine", "text": "썰매장에서 신나게 미끄러질 거야."},
        {"id": "3", "move": "compose", "text": "눈은 쓸모가 많다는 걸 알았어."},
    ]
    result = plot.fallback_plot("하늘", topic, turns)
    assert result["title"] == "하늘의 하얀 눈 이야기"
    assert [s["text"] for s in result["scenes"]] == [t["text"] for t in turns]


@pytest.mark.parametrize(
    ("text", "grade", "affiliation"),
    [("나는 햇살초등학교 3학년이야", 3, "elementary"), ("초등 이 학년", 2, "elementary"), ("홈스쿨 하는 4학년", 4, "homeschool")],
)
def test_onboarding_grade_and_affiliation(text, grade, affiliation):
    assert ob.parse_grade(text) == grade
    assert ob.parse_affiliation(text) == affiliation


def test_onboarding_nickname_and_lists():
    assert ob.parse_nickname("내 별명은 하늘이야") == "하늘"
    assert ob.parse_nickname("토끼라고 불러 줘") == "토끼"
    assert ob.parse_list("나는 공룡이랑 축구를 좋아해") == ["공룡", "축구"]
    assert ob.parse_list("과학, 역사 더 알고 싶어") == ["과학", "역사"]
