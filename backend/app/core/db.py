"""Kết nối PostgreSQL async (SQLAlchemy 2 + asyncpg).

EC-DB01 (Zero-Idle-Connection SSE): route streaming KHÔNG được ``Depends(get_db)`` ở hàm ngoài;
dùng ``short_session()`` mượn kết nối ngắn ở đầu/cuối luồng.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None

# Biến cục bộ giao dịch cho RLS (File 4 EC-DB15) và cô lập sandbox (EC-DB16).
ALLOWED_LOCAL_SETTINGS: Final = frozenset({"app.current_partner_id", "app.sandbox_namespace"})
_SETTING_VALUE_RE: Final = re.compile(r"^[A-Za-z0-9_\-:.]{1,128}$")

HealthStatus = Literal["ok", "down", "skipped"]


def init_engine(settings: Settings | None = None) -> AsyncEngine | None:
    """Tạo engine nếu có ``DATABASE_URL``; trả None nếu chưa cấu hình (dev không có PG)."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine
    cfg = settings or get_settings()
    if cfg.database_url is None:
        return None
    _engine = create_async_engine(
        cfg.database_url.get_secret_value(),
        pool_size=cfg.db_pool_size,
        max_overflow=cfg.db_max_overflow,
        pool_timeout=cfg.db_pool_timeout_s,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {"statement_timeout": str(cfg.db_statement_timeout_ms)},
        },
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    if _sessionmaker is None:
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503, "Cơ sở dữ liệu chưa được cấu hình.")
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency cho route REST ngắn. KHÔNG dùng cho SSE (EC-DB01)."""
    async with get_sessionmaker()() as session:
        yield session


@asynccontextmanager
async def short_session() -> AsyncIterator[AsyncSession]:
    """Mượn 1 session ngắn rồi trả ngay về pool (mẫu EC-DB01 cho SSE/WS)."""
    async with get_sessionmaker()() as session:
        yield session


async def set_local_setting(session: AsyncSession, key: str, value: str) -> None:
    """``SET LOCAL`` an toàn (chỉ khóa trong allowlist, giá trị đã kiểm tra) cho RLS."""
    if key not in ALLOWED_LOCAL_SETTINGS:
        raise ValueError(f"setting không được phép: {key}")
    if not _SETTING_VALUE_RE.fullmatch(value):
        raise ValueError("giá trị setting không hợp lệ")
    await session.execute(
        text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value}
    )


async def check_db(timeout_s: float | None = None) -> HealthStatus:
    """Healthcheck ``SELECT 1`` có timeout; ``skipped`` nếu chưa cấu hình DB."""
    engine = init_engine()
    if engine is None:
        return "skipped"
    try:
        async with asyncio.timeout(timeout_s or get_settings().db_healthcheck_timeout_s):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception:
        return "down"
    return "ok"
