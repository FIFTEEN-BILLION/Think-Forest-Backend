"""가짜 OpenAI 서버 — 실제 키 없이 'AI 경로가 끝까지 도는지'만 확인하려고 쓴다.

Responses API 의 구조화 출력 형태로만 답한다. 요청에 실린 JSON 스키마를 읽어
필수 필드를 채운 뒤, 티키 말투로 보이는 문자열을 넣는다. 품질 확인용이 아니다.
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import FastAPI, Request

app = FastAPI()
LINES = [
    "우와, 그렇구나! 그 이야기 더 듣고 싶어.",
    "재미있다! 그때 기분은 어땠어?",
    "좋은 생각이야. 왜 그렇게 생각했어?",
]
QUESTIONS = [
    "그렇게 생각한 까닭이 뭐야?",
    "그때 무엇을 보았어?",
    "다른 경우에는 어떻게 될까?",
]


def fill(schema: dict, depth: int = 0, root: dict | None = None) -> object:
    """스키마를 보고 형식만 맞는 값을 만든다. $ref 는 $defs 에서 찾아 푼다."""
    root = root if root is not None else schema
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return fill((root.get("$defs") or {}).get(name, {}), depth, root)
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if "anyOf" in schema:
        return fill(schema["anyOf"][0], depth, root)
    if kind == "object":
        props = schema.get("properties", {})
        return {name: fill(sub, depth + 1, root) for name, sub in props.items()}
    if kind == "array":
        items = schema.get("items", {"type": "string"})
        return [fill(items, depth + 1, root)] if depth < 3 else []
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "null":
        return None
    if "enum" in schema:
        return schema["enum"][0]
    return "티키"


def text_for(schema: dict) -> str:
    value = fill(schema)
    if isinstance(value, dict):
        for key in ("reaction", "line", "tikiLine", "restatement", "summary", "title", "body"):
            if key in value and isinstance(value[key], str):
                value[key] = LINES[hash(key) % len(LINES)]
        for key in ("question", "next_question"):
            if key in value and isinstance(value[key], str):
                value[key] = QUESTIONS[hash(key) % len(QUESTIONS)]
    return json.dumps(value, ensure_ascii=False)


@app.post("/v1/responses")
async def responses(request: Request) -> dict:
    body = await request.json()
    schema = (((body.get("text") or {}).get("format") or {}).get("schema")) or {"type": "object", "properties": {}}
    return {
        "id": f"resp_{uuid.uuid4().hex}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": body.get("model", "fake"),
        "output": [
            {
                "type": "message",
                "id": f"msg_{uuid.uuid4().hex}",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text_for(schema), "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }


@app.post("/v1/moderations")
async def moderations() -> dict:
    return {
        "id": f"modr_{uuid.uuid4().hex}",
        "model": "omni-moderation-latest",
        "results": [{"flagged": False, "categories": {}, "category_scores": {}}],
    }
