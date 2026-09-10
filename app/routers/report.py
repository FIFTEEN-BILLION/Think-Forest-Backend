"""POST /report/summary — 이번 주 부모님께."""

from __future__ import annotations

from fastapi import APIRouter

from ..prompts import report as prompt
from ..schemas.report import ReportRequest, ReportResponse
from ..services.claude import ClaudeError, call_json

router = APIRouter(prefix="/report", tags=["report"])


def _fallback(req: ReportRequest) -> ReportResponse:
    said = len(req.sentences)
    weeks = len(req.weekly_scores)
    trend = ""
    if weeks >= 2:
        first, last = req.weekly_scores[0], req.weekly_scores[-1]
        delta = (last.observe + last.reason + last.express) - (
            first.observe + first.reason + first.express
        )
        trend = " 최근 몇 주 사이 세 축 합계가 " + (
            "올랐어요." if delta > 0 else "비슷하게 유지됐어요." if delta == 0 else "조금 내렸어요."
        )
    return ReportResponse(
        ai=False,
        error="AI 미연결 — 규칙 기반 임시 요약",
        summary=(
            f"이번 주 아이가 남긴 문장은 {said}개예요.{trend} "
            "아이가 한 말을 다시 읽어주며 '그때 왜 그렇게 생각했어?'라고 물어봐 주세요."
        ),
        next="저녁 먹을 때 오늘 가장 궁금했던 것 한 가지를 아이가 직접 말하게 해보세요. 5분이면 충분해요.",
    )


@router.post("/summary", response_model=ReportResponse)
def summary(req: ReportRequest) -> ReportResponse:
    try:
        data = call_json(
            purpose="report.summary",
            system=prompt.SYSTEM,
            user=prompt.build_user(req.sentences, req.weekly_scores),
        )
        return ReportResponse(ai=True, **data)
    except ClaudeError as exc:
        resp = _fallback(req)
        resp.error = f"AI 실패({exc.code}) — 규칙 기반 폴백"
        return resp
    except Exception as exc:  # noqa: BLE001
        resp = _fallback(req)
        resp.error = f"AI 응답 형식 오류({type(exc).__name__}) — 규칙 기반 폴백"
        return resp
