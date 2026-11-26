"""Sửa theo review T12: idempotency arm, ACK bền vững, socket chậm, link bị hủy, digest trùng."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import fakeredis
import httpx

from app.core.clock import FakeClock
from app.core.errors import AppError, ErrorCode
from app.core.schemas.realtime import RealtimeRole
from app.services.realtime.container import RealtimeServices, build_services
from app.services.realtime.hub import LocalConnection, RealtimeHub
from app.services.realtime.models import GuardianLink, LinkStatus
from app.services.realtime.repository import InMemoryFamilyRepository
from app.services.realtime.senders import FakePushSender, FakeSmsSender

from ._flows import (
    arm_session,
    close_socket,
    escalate_body,
    guardian_ticket,
    open_socket,
    pair_guardian,
)
from ._helpers import ManualSleeper, signed_post, until

ARM = "/api/v1/shield/arm"
ESCALATE = "/api/v1/shield/escalate"


async def test_s2_ec07_transient_failure_does_not_poison_idempotency(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """Lần 1 gặp 503 (Redis/CAS tạm lỗi) → khóa idem + notif được nhả → retry cùng id arm được."""
    store = services.shield._store
    real = store.arm_shield
    calls = {"n": 0}

    async def flaky(*a: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
        return await real(*a, **kw)

    store.arm_shield = flaky  # type: ignore[method-assign]
    body = {
        "fiber_key": "default",
        "trigger_origin": "STATION_3_NOTIF",
        "amount_bucket_tier": "TIER_3M_10M",
        "client_event_id": "arm-evt-flaky-01",
        "notif_dedupe_hash": "b" * 64,
    }
    first = await signed_post(client, fake_clock, ARM, body)
    assert first.status_code == 503
    fake_clock.advance(1)
    second = await signed_post(client, fake_clock, ARM, body)
    assert second.status_code == 200, second.text
    assert second.json()["deduplicated"] is False
    assert second.json()["hsm_state"] == "S4_SHIELD_ARMED_24H"


async def test_s2_ec44_ack_timer_survives_pod_restart_via_due_zset(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sleeper: ManualSleeper,
    redis: fakeredis.FakeAsyncRedis,
    repo: InMemoryFamilyRepository,
) -> None:
    """Pod giữ hẹn giờ sập trong 10s → instance khác quét vs:ack:due và vẫn gửi SMS đúng 1 lần."""
    _, token = await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(sid, fake_clock))
    assert r.status_code == 202
    assert await redis.zcard("vs:ack:due") == 1
    await services.escalation.aclose()  # mô phỏng pod sập: mất hẹn giờ trong RAM

    sms2 = FakeSmsSender()
    other = build_services(
        redis=redis,
        clock=fake_clock,
        settings=services.settings,
        repo=repo,
        push=FakePushSender(),
        sms=sms2,
        sweep_interval_s=None,
    )
    try:
        assert await other.escalation.sweep_due() == 0  # chưa tới hạn
        fake_clock.advance(11)
        assert await other.escalation.sweep_due() == 1
        assert len(sms2.sent) == 1
        assert await other.escalation.sweep_due() == 0  # không gửi lần hai
    finally:
        await other.escalation.aclose()
        await other.hub.stop()
    assert token  # phiên người thân vẫn hợp lệ


async def test_ack_cancels_due_entry_so_sweeper_sends_nothing(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
) -> None:
    link_id, token = await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(sid, fake_clock))
    link = (await services.pairing.resolve_session(token)).link
    assert link.link_id == link_id
    assert await services.escalation.acknowledge(link, uuid.UUID(r.json()["escalation_id"]))
    fake_clock.advance(11)
    assert await services.escalation.sweep_due() == 0
    assert sms.sent == []


async def test_slow_socket_does_not_block_other_families(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Socket gửi treo bị gỡ sau timeout; gia đình khác vẫn nhận ngay (SLA 150/250ms)."""
    clock = FakeClock()
    hub = RealtimeHub(redis, clock, send_timeout_s=0.1)
    dev_slow, dev_ok = uuid.uuid4(), uuid.uuid4()
    got: list[dict[str, Any]] = []
    closed = asyncio.Event()

    async def hang(_: dict[str, Any]) -> None:
        await asyncio.Event().wait()

    async def ok(frame: dict[str, Any]) -> None:
        got.append(frame)

    async def on_close() -> None:
        closed.set()

    slow = LocalConnection(uuid.uuid4(), RealtimeRole.GUARDIAN, dev_slow, None, hang, on_close)
    fast = LocalConnection(uuid.uuid4(), RealtimeRole.GUARDIAN, dev_ok, None, ok)
    await hub.start()
    hub.register(slow)
    hub.register(fast)
    try:
        for dev in (dev_slow, dev_ok):
            await hub.publish(dev, RealtimeRole.GUARDIAN, {"type": "X", "payload": {}})
        await until(lambda: len(got) == 1, 0.5)  # không phải chờ socket chậm
        await asyncio.wait_for(closed.wait(), 1.0)
        assert hub.local_connection_count() == 1
        await hub.publish(dev_ok, RealtimeRole.GUARDIAN, {"type": "Y", "payload": {}})
        await until(lambda: len(got) == 2, 0.5)
    finally:
        hub.unregister(fast)
        await hub.stop()


async def test_revoked_link_closes_open_socket(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    repo: InMemoryFamilyRepository,
) -> None:
    services.ws_ping_interval_s = 0.05
    link_id, token = await pair_guardian(client, fake_clock)
    ws, task = await open_socket(services, await guardian_ticket(client, token))
    cur = repo.links[link_id]
    repo.links[link_id] = GuardianLink(
        link_id=cur.link_id,
        device_id=cur.device_id,
        guardian_alias=cur.guardian_alias,
        borrower_display_label=cur.borrower_display_label,
        status=LinkStatus.REVOKED_BY_GUARDIAN,
    )
    await asyncio.wait_for(task, 2)
    assert ws.close_code == 4401
    assert ws.sent[-1]["payload"]["code"] == "LINK_REVOKED"


async def test_s2_ec45_digest_to_live_guardian_not_repeated_on_reconnect(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    _, token = await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    g_ws, g_task = await open_socket(services, await guardian_ticket(client, token))
    old = (fake_clock.now() - timedelta(minutes=50)).isoformat()
    r = await signed_post(
        client, fake_clock, ESCALATE, escalate_body(sid, fake_clock, occurred_at=old)
    )
    assert r.status_code == 202
    await g_ws.wait_for("EVENT_ALERT_DIGEST")
    await close_socket(g_ws, g_task)
    g2, t2 = await open_socket(services, await guardian_ticket(client, token))
    assert "EVENT_ALERT_DIGEST" not in g2.types()
    await close_socket(g2, t2)
