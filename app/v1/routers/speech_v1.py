"""v1 음성 입력과 읽어주기(명세 20절).

- `POST /speech/stream-tickets` — 1회용·짧은 수명 WebSocket 접속권
- `WS   /speech/stream?ticket=…` — PCM 조각 업로드 → 중간 자막(PARTIAL_TRANSCRIPT) → 최종 문장(FINAL_TRANSCRIPT)
- `POST /speech/transcriptions` — 스트리밍이 막혔을 때 녹음 파일로 한 번 재시도
- `POST /speech/synthesis` — 티키 메시지 읽어주기(Typecast)

보관 원칙(20.5): 음성 조각은 메모리에서만 다루고 저장하지 않는다. 음성·중간 자막·ticket 을 로그에 남기지 않는다.
최종 문장도 여기서 저장하지 않는다 — 아이가 확인·수정한 뒤 대화 메시지 API 로 보낼 때 저장된다.
"""

from __future__ import annotations

import contextlib
import json

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from ... import clock
from ...auth import permission_enabled
from ...config import get_settings
from ...db import get_session
from ...models import Child
from ...safety.blocklist import find_blocked
from ...schemas.common import CamelModel
from ...services import speech as speech_service
from ...services import tts, usage
from .. import cursor, models_accounts, speech_engine, speech_tickets
from ..deps import CurrentUser, require_user
from ..errors import ApiError
from ..models import User
from ..models_conversation import ChildProfile, ConversationMessage, ConversationSession
from ..models_ops import SpeechStreamTicket

router = APIRouter(prefix="/speech", tags=["v1-speech"])

# WebSocket 이벤트 종류(명세 20.2~20.3)
PARTIAL = "PARTIAL_TRANSCRIPT"
FINAL = "FINAL_TRANSCRIPT"
ERROR = "ERROR"
WARNING = "WARNING"


# ---------------------------------------------------------------- 요청·응답 모델


class AudioFormat(CamelModel):
    encoding: str = "PCM_S16LE"
    sample_rate: int = 16000
    channels: int = 1


class StreamTicketRequest(CamelModel):
    conversation_id: str | None = Field(default=None, max_length=48)
    question_id: str | None = Field(default=None, max_length=64)
    locale: str = Field(default="ko-KR", max_length=16)
    audio: AudioFormat = Field(default_factory=AudioFormat)


class StreamTicketResponse(CamelModel):
    stream_id: str
    ticket: str
    web_socket_url: str
    expires_at: str


class TranscriptResponse(CamelModel):
    text: str
    confidence: float
    duration_ms: int | None = Field(default=None, description="길이를 알 수 없는 형식이면 null")


class SynthesisRequest(CamelModel):
    """티키가 한 말을 읽어준다. `messageId` 를 주면 서버가 그 메시지 본문을 쓴다."""

    conversation_id: str | None = Field(default=None, max_length=48)
    message_id: str | None = Field(default=None, max_length=48)
    text: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------- 공통 확인


def _has_voice_consent(child: Child) -> bool:
    """보호자가 음성 보관 동의(`voice_retention`)를 남겼는지. 동의 API(26절)가 권한의 근거다."""
    db = object_session(child)
    if db is None:
        return False
    with db.no_autoflush:
        profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == child.id))
        if profile is None:
            return False
        consent = models_accounts.active_consent(db, profile.id, "voice_retention")
    return consent is not None and consent.actor_role == "GUARDIAN"


def _require_voice(child: Child) -> None:
    """미설정 시 허용하며, 보호자가 설정에서 끈 경우에만 차단한다."""
    if not permission_enabled(child, "voice"):
        raise ApiError(
            403,
            "CONSENT_REQUIRED",
            "보호자 설정에서 음성 사용이 꺼져 있어요.",
            {"permission": "voice", "setting": "voiceEnabled"},
        )


def _require_speech_on() -> None:
    if not get_settings().speech_enabled:
        raise ApiError(503, "SPEECH_UNAVAILABLE", "지금은 음성을 쓸 수 없어요. 글로 말해 주세요.")


def _consume_quota(child: Child) -> None:
    if not usage.try_consume(child.id):
        raise ApiError(429, "RATE_LIMITED", "오늘은 충분히 이야기했어요. 내일 다시 만나요.")


# ---------------------------------------------------------------- 20.1 접속권


@router.post("/stream-tickets", response_model=StreamTicketResponse, status_code=201)
def create_stream_ticket(
    body: StreamTicketRequest,
    request: Request,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> StreamTicketResponse:
    _require_speech_on()
    _require_voice(cu.child)
    if not get_settings().websocket_enabled:
        raise ApiError(
            503,
            "STREAMING_UNAVAILABLE",
            "지금은 실시간 듣기를 쓸 수 없어요. 녹음해서 보내 주세요.",
            {"fallback": "POST /api/v1/speech/transcriptions"},
        )
    if body.audio.encoding != "PCM_S16LE" or body.audio.sample_rate != 16000 or body.audio.channels != 1:
        raise ApiError(400, "INVALID_INPUT", "16kHz mono PCM 만 보낼 수 있어요.", {"fields": ["audio"]})
    if body.conversation_id:
        session = db.get(ConversationSession, body.conversation_id)
        if session is None or session.user_id != cu.id:
            raise ApiError(404, "SESSION_NOT_FOUND", "대화를 찾을 수 없어요.")
    profile = db.scalar(select(ChildProfile).where(ChildProfile.child_id == cu.child.id))
    row, raw = speech_tickets.issue(
        db,
        cu.user,
        profile_id=profile.id if profile else None,
        conversation_id=body.conversation_id,
        question_id=body.question_id,
        locale=body.locale,
        sample_rate=body.audio.sample_rate,
    )
    db.commit()
    return StreamTicketResponse(
        stream_id=row.id,
        ticket=raw,
        web_socket_url=speech_tickets.websocket_url(str(request.base_url)),
        expires_at=cursor.iso(row.expires_at) or "",
    )


# ---------------------------------------------------------------- 20.2 스트리밍


@router.websocket("/stream")
async def stream(
    websocket: WebSocket,
    ticket: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> None:
    """START → binary PCM 조각 → PARTIAL_TRANSCRIPT … → STOP → FINAL_TRANSCRIPT.

    오류는 `{"type": "ERROR", "code", "retryable", "message"}` 로 보낸다.
    """
    await websocket.accept()
    settings = get_settings()
    if not settings.websocket_enabled:
        await _send_error(websocket, "STREAMING_UNAVAILABLE", True, "지금은 실시간 듣기를 쓸 수 없어요.")
        await websocket.close(code=1013)
        return
    try:
        row = speech_tickets.consume(db, ticket)
    except speech_tickets.TicketInvalid:
        await _send_error(websocket, "TICKET_INVALID", False, "연결이 만료됐어요. 마이크를 다시 눌러 주세요.")
        await websocket.close(code=4401)
        return

    user = db.get(User, row.user_id)
    profile = db.get(ChildProfile, row.profile_id) if row.profile_id else None
    child_id = profile.child_id if profile else user.child_id if user and not row.profile_id else None
    child = db.get(Child, child_id) if child_id else None
    if child is None:
        await _send_error(websocket, "TICKET_INVALID", False, "연결이 만료됐어요. 마이크를 다시 눌러 주세요.")
        await websocket.close(code=4401)
        return
    try:
        _require_voice(child)
    except ApiError as exc:
        await _send_error(websocket, exc.code, False, exc.message)
        await _close(db, websocket, row)
        return
    try:
        engine = speech_engine.make_engine(child)
    except speech_engine.EngineUnavailable:
        await _send_error(websocket, "AI_TEMPORARILY_UNAVAILABLE", True, "지금은 듣기가 잠깐 쉬고 있어요.")
        await _close(db, websocket, row)
        return

    try:
        await _pump(websocket, engine, row, settings)
    except WebSocketDisconnect:
        pass
    finally:
        engine.close()  # 메모리 버퍼를 비운다
        await _close(db, websocket, row)


async def _pump(websocket: WebSocket, engine, row: SpeechStreamTicket, settings) -> None:
    """한 발화의 수신 루프. 음성 바이트와 자막은 어디에도 남기지 않는다."""
    if not await _expect_start(websocket, row):
        return

    max_bytes = settings.speech_max_utterance_seconds * speech_engine.BYTES_PER_SECOND
    warn_bytes = settings.speech_warn_utterance_seconds * speech_engine.BYTES_PER_SECOND
    total = 0
    sequence = 0
    warned = False
    previous = ""

    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        chunk = message.get("bytes")
        if chunk is None:
            frame = _parse(message.get("text"))
            if frame is None:
                await _send_error(websocket, "INVALID_INPUT", False, "보낸 내용을 이해하지 못했어요.")
                return
            if frame.get("type") == "STOP":
                await _finish(websocket, engine, row)
                return
            continue  # 그 밖의 제어 메시지는 무시한다(heartbeat 등)
        total += len(chunk)
        engine.feed(chunk)
        if not warned and total >= warn_bytes:
            warned = True
            await websocket.send_json(
                {
                    "type": WARNING,
                    "code": "UTTERANCE_ENDING_SOON",
                    "retryable": True,
                    "message": "이제 슬슬 마무리해 볼까요?",
                }
            )
        if total >= max_bytes:
            # 상한을 넘으면 더 받지 않고, 여기까지 들은 내용으로 마무리한다.
            await _send_error(websocket, "UTTERANCE_TOO_LONG", True, "한 번에 1분까지 들을 수 있어요.")
            await _finish(websocket, engine, row)
            return
        text = await run_in_threadpool(_safe_partial, engine)
        if text:
            sequence += 1
            await websocket.send_json(
                {
                    "type": PARTIAL,
                    "sequence": sequence,
                    "text": text,
                    "stablePrefix": speech_engine.stable_prefix(previous, text),
                }
            )
            previous = text


async def _expect_start(websocket: WebSocket, row: SpeechStreamTicket) -> bool:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        return False
    frame = _parse(message.get("text"))
    if frame is None or frame.get("type") != "START":
        await _send_error(websocket, "INVALID_INPUT", False, "먼저 START 를 보내 주세요.")
        return False
    stream_id = frame.get("streamId")
    if stream_id is not None and stream_id != row.id:
        await _send_error(websocket, "TICKET_INVALID", False, "연결이 만료됐어요. 마이크를 다시 눌러 주세요.")
        return False
    return True


def _parse(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        frame = json.loads(raw)
    except ValueError:
        return None
    return frame if isinstance(frame, dict) else None


def _safe_partial(engine) -> str | None:
    """중간 자막은 실패해도 대화를 끊지 않는다(최종 문장에서 다시 시도한다)."""
    try:
        return engine.partial()
    except Exception:  # noqa: BLE001 — 외부 전사 실패. 원문은 남기지 않는다
        return None


async def _finish(websocket: WebSocket, engine, row: SpeechStreamTicket) -> None:
    try:
        final = await run_in_threadpool(engine.final)
    except Exception:  # noqa: BLE001 — 전사 실패는 코드로만 알린다
        await _send_error(websocket, "AI_TEMPORARILY_UNAVAILABLE", True, "지금은 듣기가 잠깐 쉬고 있어요.")
        return
    if not final.text.strip():
        await _send_error(websocket, "NO_SPEECH_DETECTED", True, "목소리를 듣지 못했어요. 다시 말해 주세요.")
        return
    await websocket.send_json(
        {
            "type": FINAL,
            "streamId": row.id,
            "text": final.text.strip(),
            "confidence": final.confidence,
            "durationMs": final.duration_ms,
        }
    )


async def _send_error(websocket: WebSocket, code: str, retryable: bool, message: str) -> None:
    await websocket.send_json({"type": ERROR, "code": code, "retryable": retryable, "message": message})


async def _close(db: Session, websocket: WebSocket, row: SpeechStreamTicket) -> None:
    row.closed_at = clock.now()
    db.commit()
    with contextlib.suppress(RuntimeError):  # 이미 닫혔다
        await websocket.close()


# ---------------------------------------------------------------- 20.3 파일 재시도


@router.post("/transcriptions", response_model=TranscriptResponse)
async def transcribe(
    file: UploadFile = File(..., description="완성된 짧은 녹음 파일"),
    cu: CurrentUser = Depends(require_user),
) -> TranscriptResponse:
    _require_speech_on()
    _require_voice(cu.child)
    mime = speech_service.base_mime(file.content_type)
    if mime not in speech_service.ALLOWED_AUDIO_TYPES:
        raise ApiError(415, "UNSUPPORTED_AUDIO", "이 소리 파일 형식은 읽을 수 없어요.", {"contentType": mime})
    data = await file.read(speech_service.MAX_AUDIO_BYTES + 1)
    if not data:
        raise ApiError(400, "INVALID_INPUT", "소리가 비어 있어요.", {"fields": ["file"]})
    if len(data) > speech_service.MAX_AUDIO_BYTES:
        raise ApiError(413, "AUDIO_TOO_LARGE", "녹음이 너무 길어요. 조금 짧게 말해 주세요.")
    try:
        # 엔진을 만들 수 있는지로 ZDR 게이트·AI 사용 가능 여부를 함께 확인한다.
        speech_engine.make_engine(cu.child).close()
    except speech_engine.EngineUnavailable as exc:
        raise ApiError(503, "AI_TEMPORARILY_UNAVAILABLE", "지금은 듣기가 잠깐 쉬고 있어요.") from exc
    _consume_quota(cu.child)
    try:
        text = await run_in_threadpool(speech_service.transcribe, data, file.filename or "speech", mime)
    except speech_service.SpeechError as exc:
        raise ApiError(503, "AI_TEMPORARILY_UNAVAILABLE", "지금은 듣기가 잠깐 쉬고 있어요.") from exc
    text = (text or "").strip()
    if not text:
        raise ApiError(422, "NO_SPEECH_DETECTED", "목소리를 듣지 못했어요. 다시 말해 주세요.")
    return TranscriptResponse(
        text=text, confidence=speech_engine.DEFAULT_CONFIDENCE, duration_ms=speech_engine.wav_duration_ms(data)
    )


# ---------------------------------------------------------------- 20.4 읽어주기


@router.post(
    "/synthesis",
    responses={200: {"content": {"audio/mpeg": {}}, "description": "읽어주기 음성(mp3)"}},
    response_class=Response,
)
def synthesize(
    body: SynthesisRequest,
    cu: CurrentUser = Depends(require_user),
    db: Session = Depends(get_session),
) -> Response:
    _require_speech_on()
    _require_voice(cu.child)
    text = _synthesis_text(db, cu, body)
    if find_blocked(text):
        raise ApiError(422, "UNSAFE_CONTENT", "이 문장은 읽어 줄 수 없어요.")
    _consume_quota(cu.child)
    try:
        audio, content_type = tts.synthesize(text)
    except tts.TtsError as exc:
        raise ApiError(503, "AI_TEMPORARILY_UNAVAILABLE", "지금은 읽어주기가 잠깐 쉬고 있어요.") from exc
    # 합성한 오디오는 저장하지 않고 그대로 흘려보낸다.
    return Response(content=audio, media_type=content_type, headers={"Cache-Control": "no-store"})


def _synthesis_text(db: Session, cu: CurrentUser, body: SynthesisRequest) -> str:
    """읽어줄 문장은 티키가 한 말이다. messageId 를 주면 서버가 본문을 찾는다."""
    if body.message_id:
        message = db.get(ConversationMessage, body.message_id)
        session = db.get(ConversationSession, message.session_id) if message else None
        if message is None or session is None or session.user_id != cu.id:
            raise ApiError(404, "MESSAGE_NOT_FOUND", "읽어 줄 메시지를 찾을 수 없어요.")
        if message.role != "ASSISTANT":
            raise ApiError(403, "FORBIDDEN", "티키가 한 말만 읽어 줄 수 있어요.")
        return message.content
    if body.text and body.text.strip():
        return body.text.strip()
    raise ApiError(400, "INVALID_INPUT", "읽어 줄 내용을 알려 주세요.", {"fields": ["messageId", "text"]})
