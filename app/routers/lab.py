"""POST /lab/activity — 호기심 실험실 관찰 활동.

1차 금칙어 필터를 AI 호출 전에 돌린다. 차단 시 생성하지 않는다.
AI 실패 시에는 규칙 기반 폴백으로 최소한의 관찰 활동을 만든다.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..prompts import lab as prompt
from ..safety import blocklist
from ..schemas.lab import LabRequest, LabResponse, QuizItem
from ..services import diagnostics
from ..services.claude import ClaudeError, call_json

router = APIRouter(prefix="/lab", tags=["lab"])


def _fallback(topic: str) -> LabResponse:
    return LabResponse(
        ai=False,
        error="AI 미연결 — 규칙 기반 임시 활동",
        title=f"'{topic}' 양 끝 관찰하기",
        ctrl_label=f"{topic}의 정도(약함 ↔ 강함)",
        ask=f"{topic}이(가) 가장 약할 때와 가장 강할 때, 무엇이 어떻게 달라지는지 두 경우를 다 살펴볼까?",
        concept=(
            f"무언가를 이해하려면 한쪽 끝만 보지 말고 양쪽 끝을 다 봐야 해요. "
            f"{topic}도 조금씩 바꿔 보면서 무엇이 함께 변하는지 찾으면, 규칙이 보이기 시작해요."
        ),
        quiz=[
            QuizItem(
                q="양 끝을 모두 관찰하면 좋은 이유는?",
                options=["더 빨리 끝나서", "변화의 규칙이 보여서", "정답이 하나여서"],
                answer=1,
            )
        ],
    )


@router.post("/activity", response_model=LabResponse)
def activity(req: LabRequest) -> LabResponse:
    hit = blocklist.check(req.topic)
    if hit.blocked:
        diagnostics.record_block(
            stage="blocklist", surface="lab", term=hit.term, reason=hit.reason
        )
        return LabResponse(
            ai=False,
            error=f"blocked:{hit.reason}",
            title="이 주제로는 활동을 만들 수 없어요",
            ctrl_label="",
            ask="",
            concept="다른 주제를 골라 볼까요? 궁금한 것을 자유롭게 적어도 좋아요.",
            quiz=[],
        )

    try:
        data = call_json(
            purpose="lab.activity",
            system=prompt.SYSTEM,
            user=prompt.build_user(req.topic, req.child),
        )
        # 프롬프트는 camelCase 키(ctrlLabel)를 쓰므로 스키마 별칭 없이 매핑.
        return LabResponse(
            ai=True,
            title=data["title"],
            ctrl_label=data.get("ctrlLabel") or data.get("ctrl_label", ""),
            ask=data["ask"],
            concept=data["concept"],
            quiz=data.get("quiz", []),
        )
    except ClaudeError as exc:
        resp = _fallback(req.topic)
        resp.error = f"AI 실패({exc.code}) — 규칙 기반 폴백"
        return resp
    except Exception as exc:  # noqa: BLE001
        resp = _fallback(req.topic)
        resp.error = f"AI 응답 형식 오류({type(exc).__name__}) — 규칙 기반 폴백"
        return resp
