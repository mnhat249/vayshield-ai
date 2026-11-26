"""Webhook B2B kích hoạt Khiên 24h bằng blind index (File 4 §4.2(B)).

Header bắt buộc: X-VayShield-Partner-Key, X-VayShield-Timestamp,
X-VayShield-Signature (sha256=HEX(HMAC_SHA256(secret, timestamp + "." + raw_body))),
Idempotency-Key (lưu 48h trên Redis).

Thứ tự: tra đối tác → chữ ký (hằng thời gian) → cửa sổ timestamp 300s → rate limit theo đối tác →
idempotency + bật Khiên (``B2BShieldService``). Kho dữ liệu (``B2BRepository``) lấy qua dependency
``get_b2b_repository`` (``SqlB2BRepository``); chưa cấu hình DB/KEK thì trả 503
(không bao giờ chấp nhận yêu cầu chưa xác thực).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from redis.asyncio import Redis

from app.core.auth_headers import PARTNER_TIMESTAMP_HEADER, PartnerCaller
from app.core.clock import Clock, get_clock
from app.core.db import get_sessionmaker
from app.core.errors import AppError, ErrorCode
from app.core.openapi import error_responses
from app.core.redis import get_redis
from app.core.schemas.b2b import DisbursementShieldRequest, DisbursementShieldResponse
from app.mule_graph.repository import B2BRepository
from app.mule_graph.repository_pg import SqlB2BRepository
from app.mule_graph.shield_service import B2BShieldService
from app.security.envelope import get_envelope_cipher
from app.security.keys import SecurityConfigError
from app.security.partner_hmac import verify_partner_request
from app.security.rate_limit import CompositeRateLimiter
from app.shield_state.store import ShieldStateStore

router = APIRouter(prefix="/api/v1/b2b", tags=["b2b"])


async def get_b2b_repository() -> B2BRepository:
    """Bản PostgreSQL khi có ``DATABASE_URL`` + ``KMS_MASTER_KEK``; thiếu → 503 (fail-closed).

    Test ghi đè bằng ``dependency_overrides`` (bản in-memory)."""
    sessionmaker = get_sessionmaker()  # 503 nếu chưa cấu hình DB
    try:
        cipher = get_envelope_cipher()
    except SecurityConfigError:
        raise AppError(
            ErrorCode.SERVICE_DEGRADED, 503, "Kho dữ liệu đối tác chưa được cấu hình."
        ) from None
    return SqlB2BRepository(sessionmaker, cipher)


def get_shield_store(redis: Annotated[Redis, Depends(get_redis)]) -> ShieldStateStore:
    return ShieldStateStore(redis)


@router.post(
    "/disbursement-shield",
    operation_id="b2bDisbursementShield",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DisbursementShieldResponse,
    responses=error_responses(401, 409),
    summary="Đối tác giải ngân → bật Khiên 24h trên thiết bị người vay (< 70ms)",
)
async def disbursement_shield(
    request: Request,
    body: DisbursementShieldRequest,
    caller: PartnerCaller,
    x_vayshield_timestamp: Annotated[
        int, Header(alias="X-VayShield-Timestamp", description="Unix giây; |now - ts| ≤ 300")
    ],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
    repo: Annotated[B2BRepository, Depends(get_b2b_repository)],
    redis: Annotated[Redis, Depends(get_redis)],
    store: Annotated[ShieldStateStore, Depends(get_shield_store)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> DisbursementShieldResponse:
    partner = await repo.get_partner_by_key(caller.partner_key)
    if partner is None or not partner.is_active:
        # Không phân biệt "khóa lạ" với "chữ ký sai" (chống dò khóa đối tác).
        raise AppError(ErrorCode.SIGNATURE_INVALID, 401)
    raw_body = await request.body()
    now = clock.now()
    verify_partner_request(
        secret=partner.hmac_secret,
        signature_header=caller.signature,
        timestamp_header=request.headers.get(PARTNER_TIMESTAMP_HEADER),
        raw_body=raw_body,
        now_s=int(now.timestamp()),
    )
    limiter = CompositeRateLimiter(
        redis, device_rpm=partner.rate_limit_rpm, ip_rpm=partner.rate_limit_rpm
    )
    await limiter.enforce(device_id=None, ip=f"partner:{partner.id}", clock=clock)
    return await B2BShieldService(repo, store, redis).disbursement_shield(
        partner, body, idempotency_key, now
    )
