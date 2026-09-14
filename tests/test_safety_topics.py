"""민감 주제 1차 필터와 별명 정리 — LLM 없이 결정론으로 막는 부분."""

import pytest
from app.safety import pii
from app.safety import topics as sensitive
from app.talks import onboarding as ob


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


def test_pii_mask_can_keep_nicknames_for_onboarding():
    assert "하늘" in pii.mask("나는 하늘이야", names=False).text
    assert "하늘" not in pii.mask("나는 하늘이야").text


def test_nickname_cleaning_rejects_masked_or_sensitive_values():
    assert ob.clean_nickname("토끼") == "토끼"
    assert ob.clean_nickname("하늘●●●") is None
    assert ob.clean_nickname("대통령") is None
    assert ob.clean_nickname("아주아주아주긴별명이에요") is None  # 10자 초과
