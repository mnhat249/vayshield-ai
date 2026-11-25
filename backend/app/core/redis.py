"""Kết nối Redis 7 async (redis-py). Test thay client bằng fakeredis qua ``set_redis_client``."""

from __future__ import annotations

import asyncio
from typing import Literal

from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode

_client: Redis | None = None

HealthStatus = Literal["ok", "down", "skipped"]


def init_redis(settings: Settings | None = None) -> Redis | None:
    """Tạo client nếu có ``REDIS_URL``; None nếu chưa cấu hình."""
    global _client
    if _client is not None:
        return _client
    cfg = settings or get_settings()
    if cfg.redis_url is None:
        return None
    timeout = cfg.redis_socket_timeout_ms / 1000
    _client = Redis.from_url(
        cfg.redis_url.get_secret_value(),
        decode_responses=True,
        socket_timeout=timeout,
        socket_connect_timeout=max(timeout, 0.5),
        health_check_interval=30,
    )
    return _client


def set_redis_client(client: Redis | None) -> None:
    """Gắn client có sẵn (vd fakeredis trong test)."""
    global _client
    _client = client


def get_redis() -> Redis:
    """FastAPI dependency / helper. 503 nếu Redis chưa cấu hình."""
    client = init_redis()
    if client is None:
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503, "Bộ nhớ đệm Redis chưa được cấu hình.")
    return client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None


async def check_redis(timeout_s: float = 0.5) -> HealthStatus:
    client = init_redis()
    if client is None:
        return "skipped"
    try:
        async with asyncio.timeout(timeout_s):
            await client.ping()
    except Exception:
        return "down"
    return "ok"
