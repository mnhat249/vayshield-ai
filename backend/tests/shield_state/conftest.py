"""Fixture riêng của tests/shield_state: Redis thật (``TEST_REDIS_URL``) cho test Lua."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis


@pytest.fixture
async def real_redis() -> AsyncIterator[Redis]:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("cần Redis thật: đặt TEST_REDIS_URL")
    client = Redis.from_url(url, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def uid() -> str:
    """Hậu tố ngẫu nhiên để test trên Redis thật không đụng khóa của nhau."""
    return uuid.uuid4().hex[:12]
