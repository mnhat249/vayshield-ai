"""Fixture dùng chung cho toàn bộ test backend (D5: file khóa ở T00).

- ``settings``: Settings môi trường test (không DB/Redis thật mặc định).
- ``redis``: fakeredis async có Lua (lupa) — gắn vào ``app.core.redis``.
- ``app`` / ``client``: app FastAPI mới mỗi test + httpx.AsyncClient (ASGITransport).
- ``fake_clock``: đồng hồ giả, đã override ``get_clock`` trong app.
- ``pg``: AsyncEngine tới PostgreSQL thật; test đánh dấu ``@pytest.mark.pg`` tự skip nếu thiếu
  ``TEST_DATABASE_URL`` (role app NOSUPERUSER NOBYPASSRLS). ``pg_admin``: role migration
  (``TEST_ADMIN_DATABASE_URL``) — chỉ cho test DDL.
- ``@pytest.mark.redis_real`` cần ``TEST_REDIS_URL``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core import redis as core_redis
from app.core.clock import FakeClock, get_clock
from app.core.config import AppEnv, Settings, get_settings
from app.main import create_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_pg = pytest.mark.skip(reason="cần PostgreSQL thật: đặt TEST_DATABASE_URL")
    skip_redis = pytest.mark.skip(reason="cần Redis thật: đặt TEST_REDIS_URL")
    for item in items:
        if "pg" in item.keywords and not TEST_DATABASE_URL:
            item.add_marker(skip_pg)
        if "redis_real" in item.keywords and not TEST_REDIS_URL:
            item.add_marker(skip_redis)


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(app_env=AppEnv.TEST, database_url=None, redis_url=None, log_json=False)


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    core_redis.set_redis_client(client)
    try:
        yield client
    finally:
        core_redis.set_redis_client(None)
        await client.aclose()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(
    settings: Settings, redis: fakeredis.FakeAsyncRedis, fake_clock: FakeClock
) -> Iterator[FastAPI]:
    application = create_app(settings)
    application.dependency_overrides[get_clock] = lambda: fake_clock
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def device_headers() -> dict[str, str]:
    """Header chữ ký thiết bị dạng hợp lệ về cú pháp (stub M0 chưa kiểm mật mã)."""
    return {
        "X-VayShield-Device-Signature": (
            "v1;device_id=019361a0-0000-7000-8000-000000000001;ts=1795543200;sig=bW9jaw"
        )
    }


@pytest.fixture
def sandbox_headers() -> dict[str, str]:
    return {"X-Sandbox-Session": "sbx_test_session_token_0001"}


@pytest.fixture
async def pg() -> AsyncIterator[AsyncEngine]:
    """Engine bằng role APP (NOSUPERUSER NOBYPASSRLS) — RLS có hiệu lực."""
    if not TEST_DATABASE_URL:
        pytest.skip("cần PostgreSQL thật: đặt TEST_DATABASE_URL")
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def pg_admin() -> AsyncIterator[AsyncEngine]:
    """Engine bằng role ADMIN — CHỈ dùng cho test migration/DDL, không dùng kiểm RLS."""
    if not TEST_ADMIN_DATABASE_URL:
        pytest.skip("cần TEST_ADMIN_DATABASE_URL (role chạy migration)")
    engine = create_async_engine(TEST_ADMIN_DATABASE_URL, pool_pre_ping=True)
    try:
        yield engine
    finally:
        await engine.dispose()
