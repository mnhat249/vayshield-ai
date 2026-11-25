"""STUB M0 — Fraud Ops workspace: cụm mule, hồ sơ SIMO, live feed SSE, đăng nhập. Logic: T15."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core import mocks
from app.core.auth_headers import OpsCaller
from app.core.config import get_settings
from app.core.openapi import WS_TICKET_MAX_LEN, WS_TICKET_MIN_LEN, error_responses
from app.core.schemas.ops import (
    MuleClusterPage,
    OpsStreamTicketResponse,
    OpsTokenRequest,
    OpsTokenResponse,
    SeverityLevel,
    SimoDossier,
)
from app.core.sse import SSE_HEADERS, SSE_MEDIA_TYPE, format_sse, with_keepalive
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

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
@stub
async def ops_token(body: OpsTokenRequest) -> OpsTokenResponse:
    return mocks.OPS_TOKEN


@router.get(
    "/mule-clusters",
    operation_id="opsMuleClusters",
    response_model=MuleClusterPage,
    responses=error_responses(401, 403),
    summary="Danh sách cụm tài khoản mồi (RBAC FRAUD_ANALYST)",
)
@stub
async def mule_clusters(
    _ops: OpsCaller,
    severity: Annotated[SeverityLevel | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> MuleClusterPage:
    return mocks.MULE_CLUSTERS


@router.get(
    "/simo-dossier",
    operation_id="opsSimoDossier",
    response_model=SimoDossier,
    responses=error_responses(401, 403, 404),
    summary="Hồ sơ SIMO cho 1 tài khoản mồi (unseal được ghi chuỗi băm kiểm toán)",
)
@stub
async def simo_dossier(
    _ops: OpsCaller,
    mule_node_id: Annotated[UUID, Query()],
    unseal: Annotated[bool, Query()] = False,
) -> SimoDossier:
    return mocks.SIMO_DOSSIER.model_copy(update={"mule_node_id": mule_node_id})


@router.get(
    "/stream-ticket",
    operation_id="opsStreamTicket",
    response_model=OpsStreamTicketResponse,
    responses=error_responses(401, 403),
    summary="Cấp vé 1 lần (TTL 45s) để mở SSE live feed bằng EventSource",
)
@stub
async def stream_ticket(_ops: OpsCaller) -> OpsStreamTicketResponse:
    return mocks.OPS_STREAM_TICKET


@router.get(
    "/live-feed",
    operation_id="opsLiveFeed",
    response_class=StreamingResponse,
    responses={**_FEED_DOC, **error_responses(401)},
    summary="SSE Live Threat Feed (xác thực bằng ?ticket= từ /ops/stream-ticket)",
)
@stub
async def live_feed(
    ticket: Annotated[
        str,
        Query(
            min_length=WS_TICKET_MIN_LEN,
            max_length=WS_TICKET_MAX_LEN,
            description="Vé 1 lần từ GET /api/v1/ops/stream-ticket (sai/đã dùng → 401 JSON)",
        ),
    ],
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        yield format_sse("ops.threat_signal", mocks.SSE_OPS_SIGNAL)

    return StreamingResponse(
        with_keepalive(events(), get_settings().sse_keepalive_interval_s),
        media_type=f"{SSE_MEDIA_TYPE}; charset=utf-8",
        headers=dict(SSE_HEADERS),
    )
