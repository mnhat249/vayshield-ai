"""Điểm vào FastAPI của VayShield AI.

Chạy dev: ``uv run uvicorn app.main:app --reload`` (trong thư mục ``backend/``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.body_limit import BodySizeLimitMiddleware, route_limits
from app.core.config import Settings, get_settings
from app.core.db import check_db, dispose_engine
from app.core.errors import TRACE_HEADER, TraceAndCatchAllMiddleware, install_error_handlers
from app.core.logging import configure_logging
from app.core.openapi import install_openapi
from app.core.redis import check_redis, close_redis
from app.core.schemas.health import HealthChecks, HealthResponse
from app.services.realtime.container import start_realtime, stop_realtime

API_DESCRIPTION = (
    "VayShield AI — bảo vệ người đi vay khỏi app vay lừa đảo, lãi cắt cổ, chiếm đoạt OTP. "
    "Lỗi luôn là JSON {error:{code,message_vi,trace_id}}. Tiền tệ: số nguyên VND. "
    "SSE: contracts/sse-events.schema.json. WebSocket: contracts/ws-protocol.json."
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await start_realtime(app)  # T12: hub WS + sweeper ACK (bỏ qua khi không có Redis)
    try:
        yield
    finally:
        try:
            await stop_realtime(app)
        finally:
            await close_redis()
            await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()
    configure_logging(cfg.log_level, cfg.log_json)
    app = FastAPI(
        title="VayShield AI API",
        version="1.0.0",
        description=API_DESCRIPTION,
        lifespan=lifespan,
        docs_url=None if cfg.is_production else "/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    # Thứ tự: middleware thêm sau nằm ngoài. CORS ngoài cùng để cả response lỗi có header CORS;
    # BodySizeLimit trong cùng (sau trace) để 413 vẫn mang trace_id.
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=cfg.body_limit_default_bytes,
        per_path=route_limits(cfg),
    )
    app.add_middleware(TraceAndCatchAllMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=False,  # EC-DB24: không dùng cookie → miễn nhiễm CSRF
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "If-None-Match",
            "Idempotency-Key",
            "X-VayShield-Device-Signature",
            "X-Sandbox-Session",
        ],
        expose_headers=[TRACE_HEADER, "ETag"],
        max_age=600,
    )
    app.include_router(api_router)

    @app.get(
        "/healthz",
        operation_id="healthz",
        tags=["ops-health"],
        response_model=HealthResponse,
        summary="Healthcheck FastAPI → PostgreSQL → Redis (luôn 200; status=degraded nếu hỏng)",
    )
    async def healthz() -> HealthResponse:
        db_status, redis_status = await check_db(), await check_redis()
        degraded = "down" in (db_status, redis_status)
        return HealthResponse(
            status="degraded" if degraded else "ok",
            version=cfg.app_version,
            environment=cfg.app_env.value,
            checks=HealthChecks(database=db_status, redis=redis_status),
        )

    install_openapi(app)
    return app


app = create_app()
