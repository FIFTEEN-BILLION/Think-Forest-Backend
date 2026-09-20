from app.v1.models_conversation import StoryRecord
from app.v1.story_engine import _body, _legacy_body, story_out


def sample():
    idea = "공기 속 수증기가 차가운 컵에 닿은 것 같아"
    return {
        "initialIdea": idea,
        "evidence": [idea, "차가운 캔에도 물방울이 맺혔어", "차가운 캔에도 물방울이 맺혔어"],
        "alternatives": [idea],
        "finalReflection": "이제 주변 공기도 살펴보고 싶어",
    }


def test_generated_story_groups_distinct_quotes_into_paragraphs():
    journey = sample()
    text = _body("얼음물 컵", journey)
    assert text.count(journey["initialIdea"]) == 1
    assert text.count(journey["evidence"][1]) == 1
    assert journey["finalReflection"] in text
    assert len(text.split("\n\n")) == 4
    assert journey["alternatives"] == [journey["initialIdea"]]


def test_legacy_auto_body_is_readable_without_changing_saved_story_or_edited_text():
    journey = sample()
    original = _legacy_body("얼음물 컵", journey)
    story = StoryRecord(
        id="sty_readable", title="나의 이야기", topic_title="얼음물 컵", topic_id="topic_ice_cup",
        session_id="cnv_readable", category="SCIENCE", summary="내가 나눈 생각", body=original,
        thought_journey=journey, favorite=False, version=1,
    )
    assert story_out(story).body == _body("얼음물 컵", journey)
    assert story.body == original and story.version == 1
    edited = "내가 직접 쓴 문장이야.\n같은 말을 일부러 반복해.\n같은 말을 일부러 반복해."
    story.body = edited
    assert story_out(story).body == edited


def test_empty_journey_keeps_activity_story_body():
    story = StoryRecord(
        id="sty_activity", title="생각 모험", topic_title="모험", session_id="act_1",
        category="IMAGINATION", summary="모험 기록", body="내가 만든 길이야.",
        thought_journey={}, favorite=False, version=1,
    )
    assert story_out(story).body == "내가 만든 길이야."
