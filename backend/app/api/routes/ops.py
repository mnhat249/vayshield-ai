"""Fraud Ops workspace: cụm mule, hồ sơ SIMO, live feed SSE, đăng nhập (File 4 §4.1, EC-OPS15).

RBAC: mọi route dữ liệu cần JWT vai trò ``FRAUD_ANALYST`` (thiếu/sai/hết hạn -> 401; JWT hợp lệ
nhưng thiếu vai trò -> 403). SSE dùng vé 1 lần TTL 45s (EventSource không gửi được header).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.copilot import service
from app.copilot.advisory import AdvisoryWriter
from app.copilot.deps import get_advisory_writer, get_ops_cipher, get_ops_repository
from app.copilot.feed import threat_signal_frames
from app.copilot.ops_auth import (
    ROLE_FRAUD_ANALYST,
    AnalystCaller,
    OpsTokenService,
    get_ops_token_service,
)
from app.copilot.repository import OpsRepository
from app.core.clock import Clock, get_clock
from app.core.config import get_settings
from app.core.openapi import WS_TICKET_MAX_LEN, WS_TICKET_MIN_LEN, error_responses
from app.core.redis import get_redis
from app.core.schemas.ops import (
    MuleClusterPage,
    OpsStreamTicketResponse,
    OpsTokenRequest,
    OpsTokenResponse,
    SeverityLevel,
    SimoDossier,
)
from app.core.sse import SSE_HEADERS, SSE_MEDIA_TYPE, with_keepalive
from app.security.envelope import EnvelopeCipher
from app.security.rate_limit import CompositeRateLimiter, client_ip
from app.security.tickets import TicketNamespace, consume_ticket, issue_ticket

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

_NO_STORE: Final = {"Cache-Control": "no-store"}
_FEED_DOC: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "SSE live threat feed (ops.threat_signal, ping). "
        "Schema: contracts/sse-events.schema.json#/$defs/OpsLiveFeedStream",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}


@router.post(
    "/auth/token",
    operation_id="opsAuthToken",
    response_model=OpsTokenResponse,
    responses=error_responses(401),
    summary="Cấp JWT Fraud Ops (password / refresh 30 ngày / đăng nhập nhanh giám khảo)",
)
async def ops_token(
    request: Request,
    body: OpsTokenRequest,
    response: Response,
    tokens: Annotated[OpsTokenService, Depends(get_ops_token_service)],
    redis: Annotated[Redis, Depends(get_redis)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> OpsTokenResponse:
    # Rate limit theo IP TRƯỚC khi chạm bộ kiểm tra mật khẩu/mã (chống dò mật khẩu).
    await CompositeRateLimiter.from_settings(redis).enforce(
        device_id=None, ip=client_ip(request.client), clock=clock
    )
    response.headers.update(_NO_STORE)
    return await tokens.exchange(body)


@router.get(
    "/mule-clusters",
    operation_id="opsMuleClusters",
    response_model=MuleClusterPage,
    responses=error_responses(401, 403),
    summary="Danh sách cụm tài khoản mồi (RBAC FRAUD_ANALYST)",
)
async def mule_clusters(
    _ops: AnalystCaller,
    repo: Annotated[OpsRepository, Depends(get_ops_repository)],
    severity: Annotated[SeverityLevel | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> MuleClusterPage:
    return await service.list_clusters(repo, severity=severity, limit=limit, cursor=cursor)


@router.get(
    "/simo-dossier",
    operation_id="opsSimoDossier",
    response_model=SimoDossier,
    responses=error_responses(401, 403, 404),
    summary="Hồ sơ SIMO cho 1 tài khoản mồi (unseal được ghi chuỗi băm kiểm toán)",
)
async def simo_dossier(
    ops: AnalystCaller,
    response: Response,
    repo: Annotated[OpsRepository, Depends(get_ops_repository)],
    cipher: Annotated[EnvelopeCipher | None, Depends(get_ops_cipher)],
    writer: Annotated[AdvisoryWriter, Depends(get_advisory_writer)],
    clock: Annotated[Clock, Depends(get_clock)],
    mule_node_id: Annotated[UUID, Query()],
    unseal: Annotated[bool, Query()] = False,
) -> SimoDossier:
    response.headers.update(_NO_STORE)  # chứa dữ liệu nhạy cảm khi unseal
    return await service.build_dossier(
        repo,
        cipher,
        writer,
        mule_node_id=mule_node_id,
        unseal=unseal,
        actor_subject=ops.subject,
        now=clock.now(),
    )


@router.get(
    "/stream-ticket",
    operation_id="opsStreamTicket",
    response_model=OpsStreamTicketResponse,
    responses=error_responses(401, 403),
    summary="Cấp vé 1 lần (TTL 45s) để mở SSE live feed bằng EventSource",
)
async def stream_ticket(
    ops: AnalystCaller,
    redis: Annotated[Redis, Depends(get_redis)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> OpsStreamTicketResponse:
    issued = await issue_ticket(
        redis,
        clock,
        namespace=TicketNamespace.OPS_STREAM,
        role=ROLE_FRAUD_ANALYST,
        subject=ops.subject,
        ttl_s=get_settings().ws_ticket_ttl_s,
    )
    return OpsStreamTicketResponse(
        ticket=issued.ticket,
        expires_at=issued.expires_at,
        ttl_seconds=issued.ttl_seconds,
        stream_path=f"/api/v1/ops/live-feed?ticket={issued.ticket}",
    )


@router.get(
    "/live-feed",
    operation_id="opsLiveFeed",
    response_class=StreamingResponse,
    responses={**_FEED_DOC, **error_responses(401)},
    summary="SSE Live Threat Feed (xác thực bằng ?ticket= từ /ops/stream-ticket)",
)
async def live_feed(
    redis: Annotated[Redis, Depends(get_redis)],
    ticket: Annotated[
        str,
        Query(
            min_length=WS_TICKET_MIN_LEN,
            max_length=WS_TICKET_MAX_LEN,
            description="Vé 1 lần từ GET /api/v1/ops/stream-ticket (sai/đã dùng → 401 JSON)",
        ),
    ],
) -> StreamingResponse:
    # Tiêu thụ vé TRƯỚC khi mở luồng: sai/đã dùng -> 401 JSON (không phải SSE).
    await consume_ticket(redis, ticket, namespace=TicketNamespace.OPS_STREAM)
    frames: AsyncIterator[str] = threat_signal_frames(redis)
    return StreamingResponse(
        with_keepalive(frames, get_settings().sse_keepalive_interval_s),
        media_type=f"{SSE_MEDIA_TYPE}; charset=utf-8",
        headers=dict(SSE_HEADERS),
    )
