"""WS hub: vé 1 lần (EC-DB12) và 2 instance qua Redis pub/sub (EC-DB13)."""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator

import fakeredis
import httpx
import pytest
from redis.asyncio import Redis

from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.schemas.family import FallbackContact
from app.core.schemas.realtime import RealtimeRole
from app.core.schemas.shield import EscalateRequest
from app.security.tickets import TicketNamespace, issue_ticket
from app.services.realtime.container import RealtimeServices, build_services
from app.services.realtime.repository import InMemoryFamilyRepository
from app.services.realtime.senders import FakePushSender, FakeSmsSender

from ._flows import close_socket, guardian_ticket, open_socket, pair_guardian
from ._helpers import FakeWebSocket

TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")


async def test_ec_db12_ticket_is_single_use_and_reconnect_needs_new_ticket(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """EC-DB12: vé dùng lại bị từ chối (accept → SERVER_ERROR → 4401); reconnect cần vé mới."""
    _, token = await pair_guardian(client, fake_clock)
    ticket = await guardian_ticket(client, token)
    ws1, t1 = await open_socket(services, ticket)

    ws2 = FakeWebSocket(ticket)
    from app.api.routes.ws import guardian_ws

    await guardian_ws(ws2, services, ticket)  # type: ignore[arg-type]
    assert ws2.accepted is True  # luôn accept rồi mới đóng (không HTTP 403)
    assert ws2.close_code == 4401
    assert ws2.sent[0]["payload"]["code"] == "TICKET_INVALID_OR_CONSUMED"
    await close_socket(ws1, t1)

    ws3, t3 = await open_socket(services, await guardian_ticket(client, token))  # vé mới → OK
    await close_socket(ws3, t3)


async def test_ec_db12_invalid_or_missing_ticket_closes_4401(
    client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    from app.api.routes.ws import guardian_ws

    for bad in (None, "short", "x" * 40, "y" * 200):
        ws = FakeWebSocket(bad)
        await guardian_ws(ws, services, bad)  # type: ignore[arg-type]
        assert ws.accepted and ws.close_code == 4401


async def test_ec_db12_ticket_ttl_45s_and_stored_hashed(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    r = await client.get("/api/v1/ws-ticket", headers={"Authorization": "Bearer gst_nope"})
    assert r.status_code == 401
    _, token = await pair_guardian(client, fake_clock)
    resp = await client.get("/api/v1/ws-ticket", headers={"Authorization": f"Bearer {token}"})
    data = resp.json()
    assert data["ttl_seconds"] == 45 and data["ws_path"] == "/ws/v1/guardian"
    keys = [k async for k in redis.scan_iter("vs:ws:ticket:*")]
    assert len(keys) == 1 and data["ticket"] not in keys[0]
    assert 40 <= await redis.ttl(keys[0]) <= 45


async def test_ws_ticket_with_guardian_token_of_revoked_link_rejected(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    repo: InMemoryFamilyRepository,
) -> None:
    from app.services.realtime.models import GuardianLink, LinkStatus

    link_id, token = await pair_guardian(client, fake_clock)
    cur = repo.links[link_id]
    repo.links[link_id] = GuardianLink(
        link_id=cur.link_id,
        device_id=cur.device_id,
        guardian_alias=cur.guardian_alias,
        borrower_display_label=cur.borrower_display_label,
        status=LinkStatus.REVOKED_BY_BORROWER,
    )
    resp = await client.get("/api/v1/ws-ticket", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def _two_instance_scenario(r1: Redis, r2: Redis) -> None:
    """Người vay ở instance #1, người thân ở instance #2, chỉ chung Redis (EC-DB13)."""
    clock = FakeClock()
    repo = InMemoryFamilyRepository()  # giả lập PostgreSQL dùng chung
    settings = Settings(app_env="test", database_url=None, redis_url=None, log_json=False)  # type: ignore[arg-type]
    push, sms = FakePushSender(), FakeSmsSender()
    inst1 = build_services(redis=r1, clock=clock, settings=settings, repo=repo, push=push, sms=sms)
    inst2 = build_services(
        redis=r2, clock=clock, settings=settings, repo=repo, push=FakePushSender(), sms=sms
    )
    device = uuid.uuid4()
    link_id = uuid.uuid4()
    await repo.create_pending(
        link_id=link_id,
        device_id=device,
        guardian_alias="Con gái",
        borrower_display_label="Bà",
        pairing_code_hash="0" * 64,
        pairing_expires_at=clock.now(),
    )
    await repo.activate(
        link_id,
        push_subscription=None,
        fallback_contact=FallbackContact(channel="SMS", value="0900000000"),
        consented_at=clock.now(),
    )
    try:
        import asyncio

        from app.api.routes.ws import guardian_ws

        g_ticket = (
            await issue_ticket(
                r2,
                clock,
                namespace=TicketNamespace.WS,
                role="GUARDIAN",
                subject=str(link_id),
                ttl_s=45,
            )
        ).ticket
        b_ticket = (
            await issue_ticket(
                r1,
                clock,
                namespace=TicketNamespace.WS,
                role="BORROWER",
                subject=str(device),
                ttl_s=45,
            )
        ).ticket
        g_ws, b_ws = FakeWebSocket(g_ticket), FakeWebSocket(b_ticket)
        g_task = asyncio.create_task(guardian_ws(g_ws, inst2, g_ticket))  # type: ignore[arg-type]
        b_task = asyncio.create_task(guardian_ws(b_ws, inst1, b_ticket))  # type: ignore[arg-type]
        await g_ws.wait_for("SERVER_HELLO")
        await b_ws.wait_for("SERVER_HELLO")
        assert await inst1.hub.is_live(device, RealtimeRole.GUARDIAN)  # hiện diện chung qua Redis

        started = time.perf_counter()
        resp = await inst1.escalation.escalate(
            device,
            EscalateRequest(
                session_id=uuid.uuid4(),
                reason="COERCION_DETECTED",  # type: ignore[arg-type]
                hsm_state="S6_COOLING_OFF_LOCK",  # type: ignore[arg-type]
                detected_axes=[],
                client_event_id="two-inst-evt-1",
                occurred_at=clock.now(),
            ),
        )
        alert = await g_ws.wait_for("EVENT_EMERGENCY_ALERT")
        assert time.perf_counter() - started < 0.25  # WS < 250ms
        assert alert["payload"]["escalation_id"] == str(resp.escalation_id)

        # ACK đến instance #2 → người vay trên instance #1 nhận ngay.
        g_ws.client_send("CLIENT_ALERT_ACK", {"escalation_id": str(resp.escalation_id)})
        acked = await b_ws.wait_for("EVENT_ESCALATION_ACKNOWLEDGED")
        assert acked["payload"]["guardian_alias"] == "Con gái"
        # Thẻ giải cứu: người thân (instance #2) → người vay (instance #1).
        from app.core.schemas.family import RescueCardType, SendRescueCardRequest

        link = await repo.get_link(link_id)
        assert link is not None
        sent = await inst2.rescue.send(
            link, SendRescueCardRequest(card_type=RescueCardType.STOP_TRANSFER_NOW)
        )
        assert sent.queued is False
        card = await b_ws.wait_for("EVENT_RESCUE_CARD")
        assert card["payload"]["card_type"] == "STOP_TRANSFER_NOW"
        g_ws.disconnect()
        b_ws.disconnect()
        await asyncio.gather(g_task, b_task)
    finally:
        for inst in (inst1, inst2):
            await inst.escalation.aclose()
            await inst.hub.stop()


async def test_ec_db13_two_instances_share_alerts_and_rescue_cards_via_pubsub() -> None:
    """EC-DB13: người vay ở instance #1, người thân ở instance #2 vẫn nhận tức thì."""
    server = fakeredis.FakeServer()
    r1 = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    r2 = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    try:
        await _two_instance_scenario(r1, r2)
    finally:
        await r1.aclose()
        await r2.aclose()


@pytest.fixture
async def real_redis_pair() -> AsyncIterator[tuple[Redis, Redis]]:
    assert TEST_REDIS_URL
    r1 = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    r2 = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        yield r1, r2
    finally:
        await r1.aclose()
        await r2.aclose()


@pytest.mark.redis_real
async def test_ec_db13_two_instances_real_redis(real_redis_pair: tuple[Redis, Redis]) -> None:
    """EC-DB13 trên Redis thật (CI): 2 kết nối/instance độc lập, cùng kịch bản."""
    await _two_instance_scenario(*real_redis_pair)
