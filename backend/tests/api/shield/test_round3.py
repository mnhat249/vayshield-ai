"""Re-review vòng 3: inflight SMS, 2 pha done, TTL hash, startup Redis chưa sẵn sàng."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import httpx
from fastapi import FastAPI

from app.core.clock import FakeClock
from app.core.config import Settings
from app.services.realtime.container import RealtimeServices, build_services, start_realtime
from app.services.realtime.repository import InMemoryFamilyRepository
from app.services.realtime.senders import FakePushSender, FakeSmsSender

from ._flows import arm_session, escalate_body, pair_guardian
from ._helpers import signed_post, until

ESC = "/api/v1/shield/escalate"


async def test_a_inflight_sms_not_duplicated_when_lease_expires_mid_send(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    redis: fakeredis.FakeAsyncRedis,
    repo: InMemoryFamilyRepository,
    settings: Settings,
) -> None:
    """Lease ZSET hết hạn khi instance 1 còn đang gửi: instance 2 thấy inflight còn hạn → bỏ qua."""
    await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    assert (
        await signed_post(client, fake_clock, ESC, escalate_body(sid, fake_clock))
    ).status_code == 202
    fake_clock.advance(11)
    gate = asyncio.Event()

    async def slow(c: Any, m: Any) -> bool:
        sms.sent.append((c, m))
        await gate.wait()
        return True

    sms.send = slow  # type: ignore[method-assign,assignment]
    t1 = asyncio.create_task(services.escalation.sweep_due())
    await until(lambda: len(sms.sent) == 1)
    fake_clock.advance(31)  # lease ZSET (30s) hết, inflight (60s) còn hạn
    svc2 = build_services(
        redis=redis,
        clock=fake_clock,
        settings=settings,
        repo=repo,
        push=FakePushSender(),
        sms=sms,
        sweep_interval_s=None,
    )
    t2 = asyncio.create_task(svc2.escalation.sweep_due())
    await asyncio.sleep(0.1)
    gate.set()
    await asyncio.gather(t1, t2)
    assert len(sms.sent) == 1
    await svc2.escalation.aclose()


async def test_b_sms_exception_does_not_lose_sms_and_escalate_still_202(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """SMS ném lỗi (timeout nhà cung cấp): /escalate vẫn 202, done KHÔNG ghi, sweeper gửi lại."""
    await pair_guardian(client, fake_clock, webpush=None)
    sid = await arm_session(client, fake_clock)

    async def boom(c: Any, m: Any) -> bool:
        raise RuntimeError("provider timeout")

    services.escalation._sms.send = boom  # type: ignore[method-assign,assignment]
    r = await signed_post(client, fake_clock, ESC, escalate_body(sid, fake_clock))
    assert r.status_code == 202, r.text
    ok = FakeSmsSender()
    services.escalation._sms = ok
    fake_clock.advance(11)
    await services.escalation.sweep_due()
    assert len(ok.sent) == 1  # không mất SMS


async def test_b_sms_returning_false_is_retried_then_exhausted_with_error_log(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    redis: fakeredis.FakeAsyncRedis,
    caplog: Any,
) -> None:
    await pair_guardian(client, fake_clock, webpush=None)
    sid = await arm_session(client, fake_clock)
    sms.ok = False
    assert (
        await signed_post(client, fake_clock, ESC, escalate_body(sid, fake_clock))
    ).status_code == 202
    for _ in range(5):
        fake_clock.advance(31)
        await services.escalation.sweep_due()
    assert len(sms.sent) == 3  # MAX_SEND_TRIES, sau đó dừng
    assert "escalation_fallback_exhausted" in caplog.text
    assert await redis.zcard("vs:ack:due") == 0


async def test_c_expired_hash_logs_error_and_creates_no_ttl_less_key(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    redis: fakeredis.FakeAsyncRedis,
    caplog: Any,
) -> None:
    await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    assert (
        await signed_post(client, fake_clock, ESC, escalate_body(sid, fake_clock))
    ).status_code == 202
    for k in await redis.keys("vs:ack:timer:*"):
        await redis.delete(k)  # mô phỏng TTL 5 phút đã hết
    fake_clock.advance(31)
    await services.escalation.sweep_due()
    assert "ack_expired_dropped" in caplog.text
    assert await redis.keys("vs:ack:timer:*") == []  # không tạo key mồ côi không TTL
    assert sms.sent == [] and await redis.zcard("vs:ack:due") == 0


async def test_d_startup_redis_not_ready_still_starts_sweeper_and_retries_hub(
    app: FastAPI,
    redis: fakeredis.FakeAsyncRedis,
    fake_clock: FakeClock,
    settings: Settings,
) -> None:
    svc = build_services(redis=redis, clock=fake_clock, settings=settings, sweep_interval_s=2.0)
    real_start = svc.hub.start
    attempts = {"n": 0}

    async def flaky_start() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("redis not ready")
        await real_start()

    svc.hub.start = flaky_start  # type: ignore[method-assign]
    app.state.realtime_services = svc
    try:
        await start_realtime(app)
        assert svc.escalation._sweeper is not None and not svc.escalation._sweeper.done()
        assert svc.hub._retry_task is not None
        await until(lambda: attempts["n"] >= 2 and svc.hub._task is not None, 5.0)
    finally:
        await svc.shutdown()
