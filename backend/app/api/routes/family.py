"""STUB M0 — Ghép đôi người thân & thẻ giải cứu 2 chiều. Logic thật: T12."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core import mocks
from app.core.auth_headers import DeviceOrSandboxCaller, GuardianCaller
from app.core.errors import AppError, ErrorCode, ErrorDetail
from app.core.openapi import error_responses
from app.core.schemas.family import (
    PairConfirmRequest,
    PairConfirmResponse,
    PairInitRequest,
    PairInitResponse,
    SendRescueCardRequest,
    SendRescueCardResponse,
)
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/family", tags=["family"])


@router.post(
    "/pair-init",
    operation_id="familyPairInit",
    status_code=status.HTTP_201_CREATED,
    response_model=PairInitResponse,
    responses=error_responses(401),
    summary="Người vay tạo mã ghép đôi 6 ký tự (TTL 10 phút, tối đa 3 lần sai)",
)
@stub
async def pair_init(body: PairInitRequest, _caller: DeviceOrSandboxCaller) -> PairInitResponse:
    return mocks.PAIR_INIT


@router.post(
    "/pair-confirm",
    operation_id="familyPairConfirm",
    response_model=PairConfirmResponse,
    responses=error_responses(404, 423),
    summary="Người thân xác nhận mã ghép đôi → nhận guardian session token",
)
@stub
async def pair_confirm(body: PairConfirmRequest) -> PairConfirmResponse:
    if not body.consent_accepted:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[ErrorDetail(field="body.consent_accepted", issue="consent_required")],
        )
    return mocks.PAIR_CONFIRM


@router.post(
    "/send-rescue-card",
    operation_id="familySendRescueCard",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SendRescueCardResponse,
    responses=error_responses(401, 404),
    summary="Người thân bắn Thẻ Giải cứu lên máy người vay (WS reverse push < 150ms)",
)
@stub
async def send_rescue_card(
    body: SendRescueCardRequest, _caller: GuardianCaller
) -> SendRescueCardResponse:
    return mocks.RESCUE_CARD
