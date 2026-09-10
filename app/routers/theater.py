"""POST /theater/script — 마음극장 대본.

순서(바꾸지 말 것 — 비용 설계이자 안전 설계):
  ① find_blocked() 로 1차 금칙어 검사  ── 반드시 Claude 호출보다 먼저
  ② 통과하면 generate_script() 호출
폴백 템플릿은 만들지 않는다. AI 가 없거나 실패하면 ai=False 와 안내 문구만 반환한다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..data.theater_library import BY_ID, LIBRARY
from ..safety.blocklist import find_blocked
from ..schemas.theater import LibraryScript, ScriptRequest, ScriptResponse
from ..services import diagnostics
from ..services.claude import generate_script

router = APIRouter(prefix="/theater", tags=["theater"])


@router.get("/library", response_model=list[LibraryScript])
def library() -> list[LibraryScript]:
    """검수 대본 6종(TH-09). 즉시 재생용이자 생성 품질의 기준선."""
    return list(LIBRARY)


@router.get("/library/{script_id}", response_model=LibraryScript)
def library_item(script_id: str) -> LibraryScript:
    script = BY_ID.get(script_id)
    if script is None:
        raise HTTPException(status_code=404, detail=f"검수 대본 '{script_id}' 없음")
    return script


@router.post("/script", response_model=ScriptResponse)
async def script(req: ScriptRequest) -> ScriptResponse:
    # ① 1차 금칙어 필터 — Claude 호출 전. 여기서 막히면 토큰 비용 0.
    blocked = find_blocked(req.keyword)
    if blocked:
        reason = f"1차 금칙어에 '{blocked}' 포함"
        diagnostics.record_block(
            stage="blocklist", surface="theater", term=blocked, reason=reason
        )
        return ScriptResponse(
            ai=False,
            safe=False,
            reason=f"부적절한 키워드로 차단되었습니다 ('{blocked}').",
        )

    # ② 통과 시에만 생성 단계로.
    return await generate_script(keyword=req.keyword, child=req.child)
