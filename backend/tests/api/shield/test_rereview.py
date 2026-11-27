"""Sửa theo re-review T12: SMS trùng, lease sweeper, sweep loop, lifespan, khóa IP, ACK thẻ."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import fakeredis
import httpx
from fastapi import FastAPI

from app.core.clock import FakeClock
from app.services.realtime.container import (
    RealtimeServices,
    build_services,
    start_realtime,
    stop_realtime,
)
from app.services.realtime.repository import InMemoryFamilyRepository
from app.services.realtime.senders import FakePushSender, FakeSmsSender, PushResult

from ._flows import (
    arm_session,
    borrower_ticket,
    close_socket,
    escalate_body,
    guardian_ticket,
    open_socket,
    pair_guardian,
)
from ._helpers import DEVICE_A, ManualSleeper, signed_post, until

ESC = "/api/v1/shield/escalate"


async def test_s2_ec44_no_duplicate_sms_when_delivery_slower_than_ack_timeout(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    push: FakePushSender,
) -> None:
    """Link A đã nhận SMS ngay; Push của B treo > 10s, sweeper chạy → A không nhận SMS lần 2."""
    await pair_guardian(
        client,
        fake_clock,
        alias="A",
        webpush=None,
        fallback={"channel": "SMS", "value": "0900000001"},
    )
    await pair_guardian(
        client, fake_clock, alias="B", fallback={"channel": "SMS", "value": "0900000002"}
    )
    sid = await arm_session(client, fake_clock)
    gate = asyncio.Event()
    orig = push.send

    async def slow(sub: Any, payload: Any) -> PushResult:
        await gate.wait()
        return await orig(sub, payload)

    push.send = slow  # type: ignore[method-assign,assignment]
    req = asyncio.create_task(signed_post(client, fake_clock, ESC, escalate_body(sid, fake_clock)))
    await until(lambda: any(c.value == "0900000001" for c, _ in sms.sent))
    fake_clock.advance(11)
    await services.escalation.sweep_due()
    gate.set()
    assert (await req).status_code == 202
    assert [c.value for c, _ in sms.sent].count("0900000001") == 1
    assert [c.value for c, _ in sms.sent].count("0900000002") == 1  # B chưa ACK → vẫn được SMS


async def test_s2_ec44_sweep_error_on_one_item_does_not_lose_others(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    repo: InMemoryFamilyRepository,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Lỗi ở 1 mã không làm mất mã khác; mã lỗi được thử lại khi lease hết hạn."""
    await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    for i in range(2):
        body = escalate_body(sid, fake_clock, client_event_id=f"esc-evt-{i:08d}")
        assert (await signed_post(client, fake_clock, ESC, body)).status_code == 202
    assert await redis.zcard("vs:ack:due") == 2
    fake_clock.advance(11)
    orig = repo.get_link
    calls = {"n": 0}

    async def flaky(link_id: uuid.UUID) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("PG blip")
        return await orig(link_id)

    repo.get_link = flaky  # type: ignore[method-assign]
    assert await services.escalation.sweep_due() == 1  # mã còn lại vẫn được xử lý
    assert len(sms.sent) == 1
    assert await redis.zcard("vs:ack:due") == 1  # mã lỗi còn giữ lease
    assert await services.escalation.sweep_due() == 0  # lease chưa hết hạn
    fake_clock.advance(31)
    assert await services.escalation.sweep_due() == 1  # retry sau lease
    assert len(sms.sent) == 2
    assert await redis.zcard("vs:ack:due") == 0


async def test_s2_ec44_sweep_loop_picks_up_due_escalations_on_another_instance(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
    repo: InMemoryFamilyRepository,
) -> None:
    """Vòng ``_sweep_loop`` của instance mới (pod sau restart) tự gửi SMS cho leo thang quá hạn."""
    await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    assert (
        await signed_post(client, fake_clock, ESC, escalate_body(sid, fake_clock))
    ).status_code == 202
    await services.escalation.aclose()  # pod cũ sập

    sleeper2 = ManualSleeper(fake_clock)
    sms2 = FakeSmsSender()
    fresh = build_services(
        redis=redis,
        clock=fake_clock,
        settings=services.settings,
        repo=repo,
        push=FakePushSender(),
        sms=sms2,
        sleeper=sleeper2.sleep,
        sweep_interval_s=2.0,
    )
    try:
        await fresh.startup()  # như lifespan: không cần nhận /escalate mới
        await until(lambda: bool(sleeper2.requested))  # vòng đầu đã chạy, đang ngủ 2s
        assert sms2.sent == []
        await sleeper2.advance(11)
        await until(lambda: len(sms2.sent) == 1)
    finally:
        await fresh.shutdown()


async def test_lifespan_starts_and_stops_realtime_when_redis_present(
    app: FastAPI, redis: fakeredis.FakeAsyncRedis, fake_clock: FakeClock
) -> None:
    async with app.router.lifespan_context(app):
        svc: RealtimeServices = app.state.realtime_services
        assert svc.escalation._sweeper is not None and not svc.escalation._sweeper.done()
    assert svc.escalation._sweeper is None


async def test_start_realtime_skipped_without_redis(app: FastAPI) -> None:
    from app.core import redis as core_redis

    core_redis.set_redis_client(None)
    await start_realtime(app)
    assert getattr(app.state, "realtime_services", None) is None
    await stop_realtime(app)


async def test_real_guardian_with_correct_code_not_blocked_by_shared_ip_guessing(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """10 lần đoán bừa từ cùng IP không chặn người thân thật nhập đúng mã."""
    init = await signed_post(
        client, fake_clock, "/api/v1/family/pair-init", {"guardian_alias": "X"}
    )
    code = init.json()["pairing_code"]
    for i in range(10):
        await client.post(
            "/api/v1/family/pair-confirm",
            json={"pairing_code": "ZZZZZ" + "23456789"[i % 8], "consent_accepted": True},
        )
    fake_clock.advance(61)  # qua cửa sổ rate limit 10/phút
    r = await client.post(
        "/api/v1/family/pair-confirm", json={"pairing_code": code, "consent_accepted": True}
    )
    assert r.status_code == 200, r.text


async def test_guardian_cannot_ack_borrowers_rescue_card(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    _, token = await pair_guardian(client, fake_clock)
    r = await client.post(
        "/api/v1/family/send-rescue-card",
        headers={"Authorization": f"Bearer {token}"},
        json={"card_type": "CALL_ME_NOW"},
    )
    card = r.json()["rescue_card_id"]
    g, t = await open_socket(services, await guardian_ticket(client, token))
    g.client_send("CLIENT_RESCUE_CARD_ACK", {"rescue_card_id": card})
    await asyncio.wait_for(t, 2)
    assert g.close_code == 4403
    assert await redis.zcard(f"vs:rescue:queue:{DEVICE_A}") == 1
    b, bt = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    assert "EVENT_RESCUE_CARD" in b.types()
    await close_socket(b, bt)


async def test_rescue_card_older_than_24h_pruned_on_connect(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    _, token = await pair_guardian(client, fake_clock)
    await client.post(
        "/api/v1/family/send-rescue-card",
        headers={"Authorization": f"Bearer {token}"},
        json={"card_type": "CALL_ME_NOW"},
    )
    fake_clock.advance(25 * 3600)
    b, bt = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    assert "EVENT_RESCUE_CARD" not in b.types()
    assert await redis.zcard(f"vs:rescue:queue:{DEVICE_A}") == 0
    await close_socket(b, bt)
