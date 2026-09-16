"""앱 생성 · CORS · 라우터 등록."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import api_docs
from .config import get_settings
from .db import init_db
from .routers import (
    categories,
    diagnostic,
    families,
    inquiry,
    lab,
    library,
    onboarding,
    progress,
    report,
    rubric,
    shares,
    speech,
    talks,
    tech,
    theater,
    voice,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(
    title="자람마을 API",
    description="아동용 AI 학습 서비스 자람마을의 백엔드. 프론트엔드의 AI 호출을 서버에서 대신한다.",
    version="0.2.0",
    lifespan=lifespan,
)

# 절대 allow_origins=["*"] 로 두지 않는다. CORS_ORIGINS 환경변수로 명시.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Accept", "Authorization"],
)

app.include_router(diagnostic.router)
app.include_router(rubric.router)
app.include_router(theater.router)
app.include_router(lab.router)
app.include_router(report.router)
app.include_router(tech.router)
app.include_router(inquiry.router)
# 생각 친구 대화 엔진
app.include_router(families.router)
app.include_router(onboarding.router)
app.include_router(talks.router)
app.include_router(categories.router)
app.include_router(library.router)
app.include_router(shares.router)
app.include_router(progress.router)
app.include_router(speech.router)
app.include_router(voice.router)

# /docs 에 한국어 설명·순서·예시를 붙인다.
api_docs.install(app)


@app.get("/health", tags=["health"])
def health() -> dict[str, bool]:
    return {"ok": True}
