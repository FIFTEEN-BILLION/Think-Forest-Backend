"""v1 공통 오류 형식과 요청 ID.

- 모든 응답 헤더에 `X-Request-Id` 를 붙인다(요청에 있으면 이어 쓴다).
- `/api/v1` 아래의 오류는 {"error": {"code", "message", "details", "requestId"}} 로 내려준다.
- 기존 경로의 오류 형식은 바꾸지 않는다.
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PREFIX = "/api/v1"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class ApiError(Exception):
    """v1 라우터에서 던지는 오류. 명세 7절의 코드만 쓴다."""

    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "") or "req_unknown"


def error_body(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}, "requestId": request_id(request)}}


_STATUS_CODES = {
    400: ("INVALID_INPUT", "입력 형식을 확인해 주세요."),
    401: ("UNAUTHORIZED", "다시 로그인해 주세요."),
    403: ("FORBIDDEN", "이 기록에 접근할 수 없어요."),
    404: ("SESSION_NOT_FOUND", "찾을 수 없어요."),
    405: ("INVALID_INPUT", "지원하지 않는 요청이에요."),
    422: ("INVALID_INPUT", "입력 형식을 확인해 주세요."),
    429: ("RATE_LIMITED", "잠시 뒤에 다시 시도해 주세요."),
    503: ("AI_TEMPORARILY_UNAVAILABLE", "티키가 잠깐 쉬고 있어요. 같은 말을 다시 보내 주세요."),
}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else f"req_{secrets.token_hex(12)}"
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(error_body(request, exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if not request.url.path.startswith(PREFIX):
            return await request_validation_exception_handler(request, exc)
        fields = [".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()]
        return JSONResponse(
            error_body(request, "INVALID_INPUT", "입력 형식을 확인해 주세요.", {"fields": fields}), status_code=400
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if not request.url.path.startswith(PREFIX):
            return await http_exception_handler(request, exc)
        code, message = _STATUS_CODES.get(exc.status_code, ("INVALID_INPUT", "요청을 처리하지 못했어요."))
        return JSONResponse(error_body(request, code, message), status_code=exc.status_code, headers=exc.headers)
