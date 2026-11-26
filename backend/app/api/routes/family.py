"""Ghép đôi người thân & thẻ giải cứu 2 chiều (File 4 §4.1, §3.1). Logic: T12."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.core.errors import AppError, ErrorCode
from app.core.openapi import error_responses
from app.core.schemas.family import (
    PairConfirmRequest,
    PairConfirmResponse,
    PairInitRequest,
    PairInitResponse,
    SendRescueCardRequest,
    SendRescueCardResponse,
)
from app.security.rate_limit import CompositeRateLimiter, client_ip
from app.services.realtime.container import BorrowerCaller, GuardianLinkCaller, Services

router = APIRouter(prefix="/api/v1/family", tags=["family"])

# Bucket IP riêng cho pair-confirm (endpoint không xác thực → chống dò mã): tách khỏi bucket chung.
PAIR_CONFIRM_BUCKET = "pair-confirm:"
PAIR_CONFIRM_RPM = 10


@router.post(
    "/pair-init",
    operation_id="familyPairInit",
    status_code=status.HTTP_201_CREATED,
    response_model=PairInitResponse,
    responses=error_responses(401),
    summary="Người vay tạo mã ghép đôi 6 ký tự (TTL 10 phút, tối đa 3 lần sai)",
)
async def pair_init(
    body: PairInitRequest, who: BorrowerCaller, services: Services
) -> PairInitResponse:
    return await services.pairing.pair_init(who.device_id, body)


@router.post(
    "/pair-confirm",
    operation_id="familyPairConfirm",
    response_model=PairConfirmResponse,
    responses=error_responses(404, 423),
    summary="Người thân xác nhận mã ghép đôi → nhận guardian session token",
)
async def pair_confirm(
    body: PairConfirmRequest, request: Request, services: Services
) -> PairConfirmResponse:
    ip = client_ip((request.client.host, request.client.port) if request.client else None)
    limiter = CompositeRateLimiter(
        services.redis, device_rpm=PAIR_CONFIRM_RPM, ip_rpm=PAIR_CONFIRM_RPM
    )
    decision = await limiter.check(
        device_id=None, ip=PAIR_CONFIRM_BUCKET + ip, clock=services.clock
    )
    if not decision.allowed:
        raise AppError(
            ErrorCode.RATE_LIMITED, 429, headers={"Retry-After": str(decision.retry_after_s)}
        )
    return await services.pairing.pair_confirm(ip, body)


@router.post(
    "/send-rescue-card",
    operation_id="familySendRescueCard",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SendRescueCardResponse,
    responses=error_responses(401, 404),
    summary="Người thân bắn Thẻ Giải cứu lên máy người vay (WS reverse push < 150ms)",
)
async def send_rescue_card(
    body: SendRescueCardRequest, link: GuardianLinkCaller, services: Services
) -> SendRescueCardResponse:
    return await services.rescue.send(link, body)
