"""GET /tech/panel — 기술·안전 패널(심사용). 읽기 전용 진단."""

from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..schemas.tech import TechPanelResponse
from ..services import diagnostics

router = APIRouter(prefix="/tech", tags=["tech"])


@router.get("/panel", response_model=TechPanelResponse)
def panel() -> TechPanelResponse:
    settings = get_settings()
    calls = diagnostics.calls()
    return TechPanelResponse(
        ai_enabled=settings.ai_enabled,
        model=settings.anthropic_model,
        call_count=len(calls),
        avg_latency_ms=diagnostics.avg_latency_ms(),
        calls=calls,
        blocks=diagnostics.blocks(),
    )
