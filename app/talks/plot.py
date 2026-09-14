"""이야기 플롯 완성본 — 아이가 문장으로 한 말만 재료로 쓴다.

AI 플롯은 장면마다 근거가 된 아이 발화 id 를 달아야 하고, 없는 id·민감 내용·너무 짧은 플롯은 버린다.
AI 를 쓸 수 없으면 아이 문장을 그대로 장면에 담은 규칙 기반 플롯을 만든다.
"""

from __future__ import annotations

from ..safety import topics as sensitive
from .planner import clip
from .topics import VISUAL_KEYS

SCENE_HEADINGS: dict[str, str] = {
    "hook": "처음 떠올린 생각",
    "tail": "생각이 자란 순간",
    "connect": "생활에서 찾은 생각",
    "challenge": "다시 생각해 본 순간",
    "imagine": "상상 속으로",
    "reason_check": "내 생각의 이유",
    "compose": "내가 정리한 이야기",
    "continue": "이어진 상상",
}
_PREFERRED = ("hook", "imagine", "reason_check", "challenge", "compose")


def fallback_plot(nickname: str, topic: dict, child_turns: list[dict]) -> dict:
    """child_turns: [{id, move(답한 질문), text}] — 문장으로 인정된 답만."""
    chosen: list[dict] = []
    for move in _PREFERRED:
        hit = next((t for t in child_turns if t["move"] == move and t not in chosen), None)
        if hit:
            chosen.append(hit)
    for turn in child_turns:
        if len(chosen) >= 3:
            break
        if turn not in chosen:
            chosen.append(turn)
    chosen.sort(key=lambda t: child_turns.index(t))
    return {
        "title": f"{nickname or '나'}의 {topic.get('title', '생각')} 이야기",
        "scenes": [
            {
                "heading": SCENE_HEADINGS.get(t["move"], "이야기 한 장면"),
                "text": clip(t["text"], 160),
                "fromTurnIds": [t["id"]],
                "visual": topic.get("visual", "star"),
            }
            for t in chosen[:5]
        ],
        "endingQuestion": topic.get("challenge") or "다음에는 무엇을 더 알아보고 싶어?",
    }


def validate_plot(raw_scenes: list[dict], title: str, ending: str, allowed_ids: set[str], topic: dict) -> dict | None:
    scenes = []
    for scene in raw_scenes[:5]:
        ids = [i for i in scene.get("from_turn_ids", []) if i in allowed_ids]
        text = clip(scene.get("text", ""), 160)
        heading = clip(scene.get("heading", ""), 30)
        if not ids or len(ids) != len(scene.get("from_turn_ids", [])) or not text:
            continue
        if sensitive.detect(text) or sensitive.detect(heading):
            continue
        visual = scene.get("visual")
        scenes.append(
            {
                "heading": heading or "이야기 한 장면",
                "text": text,
                "fromTurnIds": ids,
                "visual": visual if visual in VISUAL_KEYS else topic.get("visual", "star"),
            }
        )
    title = clip(title, 60)
    if len(scenes) < 3 or not title or sensitive.detect(title):
        return None
    ending = clip(ending, 120)
    return {
        "title": title,
        "scenes": scenes,
        "endingQuestion": ending if ending and not sensitive.detect(ending) else topic.get("challenge", ""),
    }
