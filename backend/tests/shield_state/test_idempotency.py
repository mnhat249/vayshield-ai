"""Idempotency thông báo 10 phút (S2-EC07) + B2B 48h; keyspace an toàn."""

from __future__ import annotations

import fakeredis
import pytest
from redis.asyncio import Redis

from app.core.schemas.common import AmountBucketTier
from app.shield_state.idempotency import (
    claim_b2b,
    claim_notification,
    disbursement_dedupe_hash,
)
from app.shield_state.keys import (
    B2B_IDEM_TTL_MS,
    NOTIF_IDEM_TTL_MS,
    b2b_idem_key,
    notif_idem_key,
    shield_state_key,
    shield_state_ttl_ms,
)
from app.shield_state.risk import HOUR_MS

DEV = "019361a0-0000-7000-8000-000000000001"


async def test_ec07_sms_and_push_same_disbursement_dedupe_10m(redis: Redis) -> None:
    """S2-EC07: SMS Banking + Push app NH cùng báo 1 khoản giải ngân → 1 sự kiện."""
    h_sms = disbursement_dedupe_hash(AmountBucketTier.TIER_10M_30M)
    h_push = disbursement_dedupe_hash(AmountBucketTier.TIER_10M_30M)
    assert h_sms == h_push
    first = await claim_notification(redis, DEV, h_sms, "session-1")
    dup = await claim_notification(redis, DEV, h_push, "session-2")
    assert first.first and not first.degraded
    assert not dup.first and dup.owner == "session-1"
    pttl = await redis.pttl(notif_idem_key(DEV, h_sms))
    assert 0 < pttl <= NOTIF_IDEM_TTL_MS == 600_000


async def test_ec07_different_amount_bucket_not_deduped(redis: Redis) -> None:
    a = await claim_notification(
        redis, DEV, disbursement_dedupe_hash(AmountBucketTier.TIER_3M_10M), "s1"
    )
    b = await claim_notification(
        redis, DEV, disbursement_dedupe_hash(AmountBucketTier.TIER_10M_30M), "s2"
    )
    assert a.first and b.first


async def test_b2b_webhook_retry_idempotent_48h(redis: Redis) -> None:
    first = await claim_b2b(redis, "partner_x", "idem-0001", "session-9")
    retry = await claim_b2b(redis, "partner_x", "idem-0001", "session-other")
    assert first.first and not retry.first and retry.owner == "session-9"
    pttl = await redis.pttl(b2b_idem_key("partner_x", "idem-0001"))
    assert B2B_IDEM_TTL_MS - 60_000 < pttl <= B2B_IDEM_TTL_MS == 48 * HOUR_MS


async def test_ec_db11_idempotency_when_redis_down_is_degraded_first() -> None:
    """EC-DB11: Redis mất → không chặn luồng bảo vệ; xử lý như lần đầu (join lũy đẳng)."""
    server = fakeredis.FakeServer()
    server.connected = False
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    claim = await claim_b2b(client, "p", "k-1", "s")
    assert claim.first and claim.degraded
    await client.aclose()


@pytest.mark.parametrize("bad", ["a:b", "x y", "", "vs:*", "a\nb"])
def test_key_segments_reject_injection(bad: str) -> None:
    with pytest.raises(ValueError):
        shield_state_key(bad)


def test_b2b_unsafe_idempotency_key_is_hashed() -> None:
    k = b2b_idem_key("p1", "weird key:with*chars")
    assert k.startswith("vs:idem:b2b:p1:h_") and len(k.rsplit(":", 1)[1]) == 66


def test_notif_hash_must_be_sha256_hex() -> None:
    with pytest.raises(ValueError):
        notif_idem_key(DEV, "nothex")


@pytest.mark.parametrize(
    ("expires", "ttl"),
    [
        (None, 24 * HOUR_MS),
        (1 * HOUR_MS, 24 * HOUR_MS),
        (48 * HOUR_MS, 48 * HOUR_MS),
        (500 * HOUR_MS, 72 * HOUR_MS),
    ],
)
def test_shield_state_ttl_clamped_24_72h(expires: int | None, ttl: int) -> None:
    assert shield_state_ttl_ms(0, expires) == ttl
