"""STUB M0 — Webhook B2B kích hoạt Khiên 24h bằng blind index. Logic thật: T14.

Header bắt buộc (File 4 §4.2(B)): X-VayShield-Partner-Key, X-VayShield-Timestamp,
X-VayShield-Signature (sha256=HEX(HMAC_SHA256(secret, timestamp + "." + raw_body))),
Idempotency-Key (lưu 48h). Stub chỉ kiểm tra sự có mặt, KHÔNG kiểm HMAC.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, status

from app.core import mocks
from app.core.auth_headers import PartnerCaller
from app.core.openapi import error_responses
from app.core.schemas.b2b import DisbursementShieldRequest, DisbursementShieldResponse
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/b2b", tags=["b2b"])


@router.post(
    "/disbursement-shield",
    operation_id="b2bDisbursementShield",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DisbursementShieldResponse,
    responses=error_responses(401, 409),
    summary="Đối tác giải ngân → bật Khiên 24h trên thiết bị người vay (< 70ms)",
)
@stub
async def disbursement_shield(
    body: DisbursementShieldRequest,
    _partner: PartnerCaller,
    x_vayshield_timestamp: Annotated[
        int, Header(alias="X-VayShield-Timestamp", description="Unix giây; |now - ts| ≤ 300")
    ],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> DisbursementShieldResponse:
    return mocks.B2B_SHIELD
