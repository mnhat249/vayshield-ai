"""Composite token bucket device → IP (EC-DB25)."""

from __future__ import annotations

import uuid

import fakeredis
import pytest

from app.core.clock import FakeClock
from app.core.errors import AppError, ErrorCode
from app.security.rate_limit import (
    CompositeRateLimiter,
    LimitScope,
    client_ip,
    ip_bucket_key,
)

from ._helpers import make_settings

NAT_IP = "100.64.12.34"  # dải CGNAT


async def test_ec_db25_shared_nat_ip_500_workers_not_blocked(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """500 thiết bị cùng 1 IP CGNAT, mỗi máy 5 request/phút → không ai bị chặn."""
    limiter = CompositeRateLimiter.from_settings(redis, make_settings())
    assert limiter.ip_rpm >= 50 * limiter.device_rpm
    clock = FakeClock()
    devices = [uuid.UUID(int=i + 1) for i in range(500)]
    for _ in range(5):
        for dev in devices:
            d = await limiter.check(device_id=dev, ip=NAT_IP, clock=clock)
            assert d.allowed, d
        clock.advance(0.01)


async def test_ec_db25_device_limit_applies_before_ip(redis: fakeredis.FakeAsyncRedis) -> None:
    """1 thiết bị spam bị chặn theo DEVICE; thiết bị khác cùng IP vẫn qua."""
    limiter = CompositeRateLimiter(redis, device_rpm=5, ip_rpm=250)
    clock = FakeClock()
    spammer, neighbour = uuid.UUID(int=1), uuid.UUID(int=2)
    results = [await limiter.check(device_id=spammer, ip=NAT_IP, clock=clock) for _ in range(6)]
    assert [r.allowed for r in results] == [True] * 5 + [False]
    assert results[-1].blocked_scope is LimitScope.DEVICE
    assert results[-1].retry_after_s >= 1
    assert (await limiter.check(device_id=neighbour, ip=NAT_IP, clock=clock)).allowed


async def test_ec_db25_ip_ceiling_still_caps_device_rotation(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Xoay device_id liên tục từ 1 IP vẫn chạm trần IP tổng."""
    limiter = CompositeRateLimiter(redis, device_rpm=5, ip_rpm=20)
    clock = FakeClock()
    allowed = 0
    last = None
    for i in range(30):
        last = await limiter.check(device_id=uuid.UUID(int=1000 + i), ip="1.2.3.4", clock=clock)
        allowed += last.allowed
    assert allowed == 20
    assert last is not None and last.blocked_scope is LimitScope.IP


async def test_ec_db25_rejection_does_not_burn_other_bucket(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    limiter = CompositeRateLimiter(redis, device_rpm=100, ip_rpm=2)
    clock = FakeClock()
    dev = uuid.UUID(int=7)
    for _ in range(5):
        await limiter.check(device_id=dev, ip="5.6.7.8", clock=clock)
    # IP khác: thiết bị vẫn còn gần đủ 100 token (3 lần bị chặn ở tầng IP không trừ token device)
    other_ip = [await limiter.check(device_id=dev, ip="9.9.9.9", clock=clock) for _ in range(2)]
    assert all(d.allowed for d in other_ip)
    tokens = float(await redis.hget(f"vs:rl:dev:{dev}", "t") or 0)
    assert tokens == pytest.approx(96, abs=0.01)


async def test_ec_db25_refill_over_time_and_enforce_429(redis: fakeredis.FakeAsyncRedis) -> None:
    limiter = CompositeRateLimiter(redis, device_rpm=2, ip_rpm=100)
    clock = FakeClock()
    dev = uuid.UUID(int=3)
    await limiter.enforce(device_id=dev, ip="1.1.1.1", clock=clock)
    await limiter.enforce(device_id=dev, ip="1.1.1.1", clock=clock)
    with pytest.raises(AppError) as exc:
        await limiter.enforce(device_id=dev, ip="1.1.1.1", clock=clock)
    assert exc.value.code is ErrorCode.RATE_LIMITED
    assert exc.value.status_code == 429
    assert exc.value.headers is not None and int(exc.value.headers["Retry-After"]) >= 1
    clock.advance(30)  # 2/phút → +1 token sau 30s
    await limiter.enforce(device_id=dev, ip="1.1.1.1", clock=clock)


async def test_ec_db25_anonymous_request_uses_ip_bucket_and_ip_is_hashed(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    limiter = CompositeRateLimiter(redis, device_rpm=1, ip_rpm=3)
    clock = FakeClock()
    res = [await limiter.check(device_id=None, ip="203.0.113.9", clock=clock) for _ in range(4)]
    assert [r.allowed for r in res] == [True, True, True, False]
    keys = [k async for k in redis.scan_iter(match="vs:rl:*")]
    assert keys == [ip_bucket_key("203.0.113.9")]
    assert "203.0.113.9" not in keys[0]


def test_client_ip_from_connection_only() -> None:
    assert client_ip(("10.0.0.1", 1234)) == "10.0.0.1"
    assert client_ip(None) == "unknown"


async def test_ec_db25_lagging_worker_clock_does_not_double_refill(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """ts = max(ts, now): worker có đồng hồ chậm 30s không làm bucket nạp lại 2 lần."""
    from datetime import timedelta

    limiter = CompositeRateLimiter(redis, device_rpm=60, ip_rpm=10_000)
    dev = uuid.UUID(int=11)
    behind = FakeClock()
    ahead = FakeClock(behind.now() + timedelta(seconds=30))
    await limiter.check(device_id=dev, ip="1.1.1.1", clock=ahead)  # 59, ts = T+30
    await limiter.check(device_id=dev, ip="1.1.1.1", clock=behind)  # 58, ts giữ T+30
    await limiter.check(device_id=dev, ip="1.1.1.1", clock=ahead)  # không nạp thêm → 57
    tokens = float(await redis.hget(f"vs:rl:dev:{dev}", "t") or 0)
    assert tokens == pytest.approx(57, abs=0.01)


async def test_ec_db25_redis_down_fails_closed_503() -> None:
    class BrokenRedis:
        async def eval(self, *a: object, **k: object) -> object:
            raise ConnectionError("down")

    limiter = CompositeRateLimiter(BrokenRedis(), device_rpm=5, ip_rpm=5)  # type: ignore[arg-type]
    with pytest.raises(AppError) as exc:
        await limiter.check(device_id=uuid.UUID(int=1), ip="1.1.1.1", clock=FakeClock())
    assert exc.value.status_code == 503
    assert exc.value.code is ErrorCode.SERVICE_DEGRADED
