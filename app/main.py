"""앱 생성 · CORS · 라우터 등록."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import diagnostic, lab, report, rubric, tech, theater

settings = get_settings()

app = FastAPI(
    title="자람마을 API",
    description="아동용 AI 학습 서비스 자람마을의 백엔드. 프론트엔드의 Claude 호출을 서버에서 대신한다.",
    version="0.1.0",
)

# 절대 allow_origins=["*"] 로 두지 않는다. CORS_ORIGINS 환경변수로 명시.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Accept"],
)

app.include_router(diagnostic.router)
app.include_router(rubric.router)
app.include_router(theater.router)
app.include_router(lab.router)
app.include_router(report.router)
app.include_router(tech.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, bool]:
    return {"ok": True}
