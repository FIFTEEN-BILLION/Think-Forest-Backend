"""아이 음성 입력.

- POST /speech/realtime-sessions: 말하는 동안 글자를 바로 보여 주는 실시간 인식용 임시 키
- POST /speech/transcriptions: 녹음이 끝난 짧은 음성 → 문장(확인·수정 뒤 대화로 보낸다)
보호자의 '음성' 권한과 ZDR(child 모드) 또는 성인 테스터 계정이 필요하다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..auth import ai_block_reason, require_child, require_permission
from ..config import get_settings
from ..models import Child
from ..schemas.library import RealtimeSessionResponse, TranscriptionResponse
from ..services import speech, usage

router = APIRouter(prefix="/speech", tags=["speech"])


def _guard(child: Child) -> None:
    if not get_settings().speech_enabled:
        raise HTTPException(status_code=503, detail="speech_disabled")
    require_permission(child, "voice")
    reason = ai_block_reason(child)
    if reason:
        raise HTTPException(status_code=403, detail=reason)
    if not usage.try_consume(child.id):
        raise HTTPException(status_code=429, detail="daily_limit")


@router.post("/transcriptions", response_model=TranscriptionResponse)
async def transcribe(file: UploadFile = File(...), child: Child = Depends(require_child)) -> TranscriptionResponse:
    mime = speech.base_mime(file.content_type)
    if mime not in speech.ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="unsupported_audio")
    _guard(child)
    data = await file.read(speech.MAX_AUDIO_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="empty_audio")
    if len(data) > speech.MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="audio_too_large")
    try:
        text = speech.transcribe(data, file.filename or "speech", mime)
    except speech.SpeechError as exc:
        raise HTTPException(status_code=502, detail=f"transcription_failed:{exc.code}") from exc
    return TranscriptionResponse(text=text)


@router.post("/realtime-sessions", response_model=RealtimeSessionResponse)
def realtime_session(child: Child = Depends(require_child)) -> RealtimeSessionResponse:
    _guard(child)
    try:
        secret = speech.create_realtime_secret()
    except speech.SpeechError as exc:
        raise HTTPException(status_code=502, detail=f"realtime_unavailable:{exc.code}") from exc
    return RealtimeSessionResponse(
        client_secret=secret["value"], expires_at=secret["expires_at"], model=secret["model"]
    )
