"""Idempotency: thông báo giải ngân 10 phút (S2-EC07) và webhook B2B 48h (File 4 §3.1).

Khi Redis không phản hồi (EC-DB11) → coi là lần đầu và gắn ``degraded=True``. An toàn vì mọi
thao tác HSM phía sau là join-semilattice (lũy đẳng): xử lý trùng không tạo 2 phiên,
không hạ rủi ro.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.shield_state.keys import (
    B2B_IDEM_TTL_MS,
    NOTIF_IDEM_TTL_MS,
    b2b_idem_key,
    load_lua,
    notif_idem_key,
)

DEFAULT_REDIS_TIMEOUT_MS: Final = 80


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    first: bool
    owner: str
    degraded: bool = False


def disbursement_dedupe_hash(amount_bucket_tier: str, direction: str = "CREDIT") -> str:
    """Băm ``(NormalizedAmountBucket, hướng)`` — SMS Banking và Push của app NH cho cùng khoản tiền
    ra cùng hash. Cửa sổ thời gian là cửa sổ TRƯỢT 10 phút do TTL của khóa đảm nhiệm (không chia
    bucket cố định để tránh lọt trùng khi 2 tin rơi 2 bên ranh giới bucket)."""
    raw = f"vs-disb|{direction}|{amount_bucket_tier}".encode()
    return hashlib.sha256(raw).hexdigest()


async def claim_idempotency(
    redis: Redis, key: str, owner: str, ttl_ms: int, *, timeout_ms: int = DEFAULT_REDIS_TIMEOUT_MS
) -> IdempotencyClaim:
    script = redis.register_script(load_lua("idempotency_claim.lua"))
    try:
        async with asyncio.timeout(timeout_ms / 1000):
            res = await script(keys=[key], args=[owner, ttl_ms])
    except (TimeoutError, RedisError, OSError):
        return IdempotencyClaim(first=True, owner=owner, degraded=True)
    first, stored = res[0], res[1]
    return IdempotencyClaim(first=int(first) == 1, owner=str(stored))


async def claim_notification(
    redis: Redis,
    device_id: str,
    dedupe_hash: str,
    owner: str,
    *,
    timeout_ms: int = DEFAULT_REDIS_TIMEOUT_MS,
) -> IdempotencyClaim:
    return await claim_idempotency(
        redis,
        notif_idem_key(device_id, dedupe_hash),
        owner,
        NOTIF_IDEM_TTL_MS,
        timeout_ms=timeout_ms,
    )


async def claim_b2b(
    redis: Redis,
    partner_id: str,
    idempotency_key: str,
    owner: str,
    *,
    ttl_ms: int = B2B_IDEM_TTL_MS,
    timeout_ms: int = DEFAULT_REDIS_TIMEOUT_MS,
) -> IdempotencyClaim:
    return await claim_idempotency(
        redis, b2b_idem_key(partner_id, idempotency_key), owner, ttl_ms, timeout_ms=timeout_ms
    )
