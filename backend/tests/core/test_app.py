"""Khung app: healthz, CORS chặt, router đủ 10 module, lockfile."""

from __future__ import annotations

import tomllib
from pathlib import Path

import fakeredis
import httpx
import pytest

from app.api.router import ROUTE_MODULES
from app.core.db import ALLOWED_LOCAL_SETTINGS, check_db
from app.core.redis import check_redis

BACKEND = Path(__file__).resolve().parents[2]


async def test_healthz_200(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "skipped", "redis": "ok"}


async def test_healthz_degraded_still_200_when_redis_down(
    client: httpx.AsyncClient, redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _down() -> bool:
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis, "ping", _down)
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"
    assert resp.json()["checks"]["redis"] == "down"


async def test_ec_db24_cors_allows_only_configured_origin(client: httpx.AsyncClient) -> None:
    ok = await client.options(
        "/api/v1/shield/arm",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-credentials" not in ok.headers

    bad = await client.options(
        "/api/v1/shield/arm",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in bad.headers


def test_router_includes_ten_route_modules() -> None:
    names = {m.__name__.rsplit(".", 1)[-1] for m in ROUTE_MODULES}
    assert names == {
        "devices",
        "policies",
        "audits",
        "shield",
        "family",
        "ws",
        "triage",
        "b2b",
        "ops",
        "sandbox",
    }


def test_ec_ops19_dependencies_locked_with_uv_lock() -> None:
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == ">=3.12,<3.13"
    lock = (BACKEND / "uv.lock").read_text(encoding="utf-8")
    for dep in ("fastapi", "pydantic", "sqlalchemy", "asyncpg", "redis", "fakeredis", "lupa"):
        assert f'name = "{dep}"' in lock, dep


async def test_db_health_skipped_without_database_url() -> None:
    assert await check_db() == "skipped"


async def test_redis_fixture_supports_lua(redis: fakeredis.FakeAsyncRedis) -> None:
    script = "redis.call('SET', KEYS[1], ARGV[1]); return redis.call('INCR', KEYS[1])"
    assert await redis.eval(script, 1, "vs:test:lua", "41") == 42
    assert await check_redis() == "ok"


def test_rls_local_settings_allowlist() -> None:
    assert {"app.current_partner_id", "app.sandbox_namespace"} == ALLOWED_LOCAL_SETTINGS


@pytest.mark.pg
async def test_pg_select_one(pg: object) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(pg, AsyncEngine)
    async with pg.connect() as conn:
        assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
