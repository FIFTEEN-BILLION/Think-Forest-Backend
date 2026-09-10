"""GET /tech/panel — 기술·안전 패널(심사용) 읽기 전용 진단."""

from __future__ import annotations

from pydantic import Field

from .common import CamelModel


class CallLogEntry(CamelModel):
    purpose: str
    model: str
    latency_ms: int
    ok: bool
    code: str = Field(description="성공/실패 코드")
    at: str = Field(description="ISO 타임스탬프")


class BlockLogEntry(CamelModel):
    stage: str = Field(description="차단 단계: blocklist(1차) 또는 ai_review(2차)")
    surface: str = Field(description="어느 기능에서 걸렸는지: theater / lab")
    term: str | None = Field(default=None, description="걸린 금칙어(1차만)")
    reason: str = Field(description="차단 사유")
    at: str


class TechPanelResponse(CamelModel):
    ai_enabled: bool
    model: str
    call_count: int
    avg_latency_ms: int
    calls: list[CallLogEntry]
    blocks: list[BlockLogEntry]
