"""Dây nối FastAPI cho Sandbox: dựng manager/simulator và ghi đè ``get_sandbox_resolver`` (T12).

``install_sandbox(app, cfg)`` được ``create_app`` gọi đúng một lần: lưu ``Settings`` của app và đặt
``dependency_overrides[get_sandbox_resolver]`` để mọi route dùng ``BorrowerCaller`` (shield, audits,
ws-ticket...) nhận phiên ``X-Sandbox-Session`` với principal có ``sandbox_namespace`` riêng.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, FastAPI
from fastapi.requests import HTTPConnection
from redis.asyncio import Redis

from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.redis import get_redis
from app.sandbox.budget import BudgetBreaker
from app.sandbox.service import SandboxSimulator
from app.sandbox.session import SandboxSessionManager
from app.services.realtime.container import (
    RealtimeServices,
    SandboxPrincipalResolver,
    conn_ip,
    get_sandbox_resolver,
)
from app.services.realtime.models import BorrowerPrincipal


def _settings_of(conn: HTTPConnection) -> Settings:
    cfg: Settings | None = getattr(conn.app.state, "sandbox_settings", None)
    return cfg or get_settings()


async def get_sandbox_manager(
    conn: HTTPConnection,
    redis: Annotated[Redis, Depends(get_redis)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> SandboxSessionManager:
    cfg = _settings_of(conn)
    return SandboxSessionManager(redis, clock, cfg, BudgetBreaker(redis, cfg, clock))


SandboxManager = Annotated[SandboxSessionManager, Depends(get_sandbox_manager)]


def build_simulator(
    manager: SandboxSessionManager, services: RealtimeServices, clock: Clock
) -> SandboxSimulator:
    return SandboxSimulator(
        redis=services.redis,
        clock=clock,
        shield=services.shield,
        ttl_s=manager.settings.sandbox_session_ttl_s,
    )


# Allowlist route cho phiên Sandbox (mặc định TỪ CHỐI). Chỉ các route không có side-effect ra
# ngoài (SMS/Push/email/webhook): /family/*, /shield/escalate, ws-ticket... đều bị 401.
ALLOWED_PATHS: Final = frozenset(
    {
        "/api/v1/shield/arm",
        "/api/v1/shield/evaluate-response",
        "/api/v1/triage/golden-hour-dossier",
        "/api/v1/tts/synthesize-vi",
        "/api/v1/policies/latest",
    }
)
ALLOWED_PREFIXES: Final = ("/api/v1/audits/",)


def path_allowed(path: str) -> bool:
    return path in ALLOWED_PATHS or path.startswith(ALLOWED_PREFIXES)


async def sandbox_resolver_dependency(
    conn: HTTPConnection, manager: SandboxManager
) -> SandboxPrincipalResolver:
    path = conn.url.path
    ip = conn_ip(conn)

    async def resolve(token: str) -> BorrowerPrincipal | None:
        if not path_allowed(path):
            return None
        return await manager.resolve(token, ip)

    return resolve


def install_sandbox(app: FastAPI, cfg: Settings) -> None:
    app.state.sandbox_settings = cfg
    app.dependency_overrides[get_sandbox_resolver] = sandbox_resolver_dependency
