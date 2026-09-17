"""그림자 미션 모형·판정 규칙 테스트. LLM 없이 결정론 부분만 확인한다."""

from itertools import product

import pytest
from app.missions import shadow as s


def test_table_covers_every_combination_with_positive_length():
    table = s.length_table()
    assert len(table) == 24
    assert all(row["length"] > 0 for row in table)


@pytest.mark.parametrize("var", s.VARIABLES)
def test_truth_holds_for_every_fair_increase(var):
    others = [v for v in s.VARIABLES if v != var]
    for combo in product(*(s.LEVELS[v] for v in others)):
        context = dict(zip(others, combo, strict=True))
        levels = s.LEVELS[var]
        for low, high in zip(levels, levels[1:], strict=False):
            up = s.run_experiment({**context, var: low}, {**context, var: high})
            down = s.run_experiment({**context, var: high}, {**context, var: low})
            assert up.fair and up.normalized_effect == s.TRUTH[var]
            assert down.fair and down.normalized_effect == s.TRUTH[var]


def test_confounded_experiment_has_no_normalized_effect():
    exp = s.run_experiment(s.BASE_SETUP, {**s.BASE_SETUP, "lightHeight": "high", "brightness": "bright"})
    assert exp.changed == ("lightHeight", "brightness")
    assert exp.fair is False
    assert exp.normalized_effect is None


def test_invalid_setup_is_rejected():
    with pytest.raises(ValueError):
        s.shadow_length({**s.BASE_SETUP, "lightHeight": "sky"})


BELIEF = s.get_belief("brightness_longer")
FAIR_BRIGHT = s.run_experiment(s.BASE_SETUP, {**s.BASE_SETUP, "brightness": "bright"})
UNFAIR_BRIGHT = s.run_experiment(s.BASE_SETUP, {**s.BASE_SETUP, "brightness": "bright", "lightHeight": "high"})
FAIR_LIGHT = s.run_experiment(s.BASE_SETUP, {**s.BASE_SETUP, "lightHeight": "high"})
RIGHT = s.Claim("brightness", "same")


@pytest.mark.parametrize(
    ("cards", "claim", "uses", "missing"),
    [
        ([], RIGHT, True, "evidence"),
        ([UNFAIR_BRIGHT], RIGHT, True, "fairness"),
        ([FAIR_LIGHT], RIGHT, True, "variable"),
        ([FAIR_BRIGHT], None, True, "variable"),
        ([FAIR_BRIGHT], s.Claim("lightHeight", "shorter"), True, "variable"),
        ([FAIR_BRIGHT], s.Claim("brightness", "longer"), True, "direction"),
        ([FAIR_BRIGHT], RIGHT, False, "evidence"),
    ],
)
def test_friend_is_never_convinced_without_fair_matching_evidence(cards, claim, uses, missing):
    verdict = s.judge_teaching(BELIEF, cards, claim, uses)
    assert verdict.convinced is False
    assert verdict.missing == missing


def test_friend_is_convinced_by_fair_card_even_when_reversed():
    belief = s.get_belief("light_higher_longer")
    reversed_card = s.run_experiment({**s.BASE_SETUP, "lightHeight": "high"}, s.BASE_SETUP)
    verdict = s.judge_teaching(belief, [UNFAIR_BRIGHT, reversed_card], s.Claim("lightHeight", "shorter"), True)
    assert verdict.convinced is True
    assert verdict.card == reversed_card


def test_choose_belief_rejects_unknown_or_identical_ideas():
    child = [s.Claim("lightHeight", "longer")]
    belief, used = s.choose_belief("made_up", "longer", child)
    assert used is False and belief.id in s.BELIEF_IDS
    belief, used = s.choose_belief("light_higher_longer", "longer", child)
    assert used is False and belief.id != "light_higher_longer"
    belief, used = s.choose_belief("distance_irrelevant", "longer", child)
    assert used is True and belief.id == "distance_irrelevant"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("빛을 높이면 그림자가 짧아져요", [("lightHeight", "shorter")]),
        ("빛을 낮추면 그림자가 길어져", [("lightHeight", "shorter")]),
        ("밝기를 바꿔도 길이가 같았어", [("brightness", "same")]),
        ("막대기가 커지면 그림자가 길어져", [("stickHeight", "longer")]),
        ("", []),
    ],
)
def test_rule_parser_normalizes_direction(text, expected):
    assert [(c.variable, c.effect) for c in s.parse_claims(text)] == expected


def test_leak_check_flags_rules_but_not_friend_lines_or_templates():
    assert all(s.leaks_answer(line) for line in s.FACT_LINES.values())
    assert not any(s.leaks_answer(b.line) for b in s.FRIEND_BELIEFS)
    words = s.belief_words(BELIEF)
    templates = [t.format(**words) for t in (*s.TEACH_PROBES.values(), s.TEACH_HINT, s.TEACH_EXPLANATION)]
    assert not any(s.leaks_answer(t) for t in templates)


def test_challenge_judgments():
    tall = s.get_challenge("tall_stick")
    right = s.get_challenge("low_light_correct")
    confound = s.get_challenge("confounded_claim")
    assert tall.friend_correct is False and s.judgment_correct(tall, "disagree")
    assert right.friend_correct is True and s.judgment_correct(right, "agree")
    assert not s.judgment_correct(right, "disagree")
    assert s.judgment_correct(confound, "unsure") and not s.judgment_correct(confound, "agree")


def test_fallback_challenge_targets_untested_variable_or_confound():
    assert s.fallback_challenge([FAIR_BRIGHT, FAIR_LIGHT], convinced=False).id == "tall_stick"
    assert s.fallback_challenge([FAIR_LIGHT], convinced=True).id == "confounded_claim"


def test_mission_payload_matches_bank():
    payload = s.mission_payload()
    assert {b["id"] for b in payload["friendBeliefs"]} == set(s.BELIEF_IDS)
    assert len(payload["table"]) == 24
