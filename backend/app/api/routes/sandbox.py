"""Judge Sandbox Mode (File 4 §4.1, EC-DB16; File 6 §1.2 mục 3; File 5 §2.1).

Logic nằm ở ``app.sandbox``.

``simulate-event`` chạy qua lõi thật (ShieldService T12 / ShieldStateStore T05) trong namespace cô
lập của phiên; mọi thứ là một request HTTPS thường (không cần WebSocket → qua được proxy doanh
nghiệp, EC-OPS05).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis

from app.core.auth_headers import SandboxCaller
from app.core.clock import Clock, get_clock
from app.core.errors import AppError, ErrorCode
from app.core.openapi import error_responses
from app.core.redis import get_redis
from app.core.schemas.sandbox import (
    SandboxSessionCreateRequest,
    SandboxSessionCreateResponse,
    SimulateEventRequest,
    SimulateEventResponse,
)
from app.sandbox.budget import micro_to_usd
from app.sandbox.deps import SandboxManager, build_simulator
from app.sandbox.session import EVENT_COST_MICRO
from app.security.rate_limit import CompositeRateLimiter
from app.services.realtime.container import Services, conn_ip

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])


@router.post(
    "/sessions",
    operation_id="sandboxCreateSession",
    status_code=status.HTTP_201_CREATED,
    response_model=SandboxSessionCreateResponse,
    responses=error_responses(),
    summary="Tạo phiên Sandbox ephemeral (mỗi tab 1 namespace; vip_code → JUDGE_VIP)",
)
async def create_session(
    body: SandboxSessionCreateRequest,
    request: Request,
    manager: SandboxManager,
    redis: Annotated[Redis, Depends(get_redis)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> SandboxSessionCreateResponse:
    ip = conn_ip(request)
    await CompositeRateLimiter.from_settings(redis, manager.settings).enforce(
        device_id=None, ip=ip, clock=clock
    )
    created = await manager.create(body.vip_code, ip)
    return SandboxSessionCreateResponse(
        sandbox_session_token=created.token,
        sandbox_namespace=created.session.namespace,
        tier=created.session.tier,
        expires_at=created.session.expires_at,
        daily_budget_remaining_usd=micro_to_usd(created.remaining_micro),
    )


@router.post(
    "/simulate-event",
    operation_id="sandboxSimulateEvent",
    response_model=SimulateEventResponse,
    responses=error_responses(401),
    summary="Bắn sự kiện ngoại vi giả lập chạy xuyên lõi thật trong namespace cô lập",
)
async def simulate_event(
    body: SimulateEventRequest,
    request: Request,
    caller: SandboxCaller,
    manager: SandboxManager,
    services: Services,
    clock: Annotated[Clock, Depends(get_clock)],
) -> SimulateEventResponse:
    session = await manager.lookup(caller.sandbox_session)
    if session is None:
        raise AppError(ErrorCode.UNAUTHORIZED, 401)
    ip = conn_ip(request)
    # Limiter TRƯỚC, trừ ngân sách SAU: request bị 429 không được tiêu kho chung.
    await services.limiter.enforce(device_id=session.device_id, ip=ip, clock=clock)
    await manager.charge(session, ip, EVENT_COST_MICRO)
    return await build_simulator(manager, services, clock).simulate(session, body)
