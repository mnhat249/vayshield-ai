"""STUB M0 — Judge Sandbox Mode. Logic thật: T16 (chạy qua lõi thật trong namespace cô lập)."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core import mocks
from app.core.auth_headers import SandboxCaller
from app.core.openapi import error_responses
from app.core.schemas.sandbox import (
    SandboxSessionCreateRequest,
    SandboxSessionCreateResponse,
    SimulateEventRequest,
    SimulateEventResponse,
)
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox"])


@router.post(
    "/sessions",
    operation_id="sandboxCreateSession",
    status_code=status.HTTP_201_CREATED,
    response_model=SandboxSessionCreateResponse,
    responses=error_responses(),
    summary="Tạo phiên Sandbox ephemeral (mỗi tab 1 namespace; vip_code → JUDGE_VIP)",
)
@stub
async def create_session(body: SandboxSessionCreateRequest) -> SandboxSessionCreateResponse:
    return mocks.SANDBOX_SESSION


@router.post(
    "/simulate-event",
    operation_id="sandboxSimulateEvent",
    response_model=SimulateEventResponse,
    responses=error_responses(401),
    summary="Bắn sự kiện ngoại vi giả lập chạy xuyên lõi thật trong namespace cô lập",
)
@stub
async def simulate_event(
    body: SimulateEventRequest, _caller: SandboxCaller
) -> SimulateEventResponse:
    return mocks.SIMULATE_EVENT
