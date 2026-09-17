"""텍스트를 음성으로 읽어준다 (Typecast TTS, voice_id 는 설정에 고정).

보호자의 '음성' 권한이 필요하다(speech 라우터와 같은 permission key 재사용).
아이가 직접 쓴 문장이 아니라 앱이 들려주는 문장(질문·안내 등)이 주 대상이라
ai_block_reason(OpenAI ZDR 게이트)은 적용하지 않는다. 다만 외부로 나가는
텍스트이므로 1차 금칙어 필터는 그대로 통과시킨다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from ..auth import require_child, require_permission
from ..models import Child
from ..safety.blocklist import find_blocked
from ..schemas.library import SynthesizeRequest
from ..services import tts, usage

router = APIRouter(prefix="/voice", tags=["voice"])


@router.post("/synthesize")
def synthesize(body: SynthesizeRequest, child: Child = Depends(require_child)) -> Response:
    require_permission(child, "voice")
    blocked = find_blocked(body.text)
    if blocked:
        raise HTTPException(status_code=403, detail="blocked_content")
    if not usage.try_consume(child.id):
        raise HTTPException(status_code=429, detail="daily_limit")
    try:
        audio, content_type = tts.synthesize(body.text)
    except tts.TtsError as exc:
        raise HTTPException(status_code=502, detail=f"synthesis_failed:{exc.code}") from exc
    return Response(content=audio, media_type=content_type)
