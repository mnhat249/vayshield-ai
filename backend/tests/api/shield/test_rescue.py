"""POST /family/send-rescue-card: WS reverse push < 150ms, xếp hàng khi người vay offline."""

from __future__ import annotations

import time
import uuid

import httpx

from app.core.clock import FakeClock
from app.services.realtime.container import RealtimeServices

from ._flows import (
    arm_session,
    borrower_ticket,
    close_socket,
    escalate_body,
    open_socket,
    pair_guardian,
)
from ._helpers import DEVICE_A, DEVICE_B, signed_post

CARD = "/api/v1/family/send-rescue-card"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_rescue_card_delivered_to_live_borrower_under_150ms(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    _, token = await pair_guardian(client, fake_clock)
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    started = time.perf_counter()
    r = await client.post(CARD, json={"card_type": "STOP_TRANSFER_NOW"}, headers=_auth(token))
    elapsed = time.perf_counter() - started
    assert r.status_code == 202, r.text
    assert elapsed < 0.150
    assert r.json()["delivered_via"] == ["WEBSOCKET"] and r.json()["queued"] is False
    frame = await b_ws.wait_for("EVENT_RESCUE_CARD")
    p = frame["payload"]
    assert p["card_type"] == "STOP_TRANSFER_NOW"
    assert p["guardian_alias"] == "Con trai"
    assert p["tts_script_vi"] and p["message_vi"]
    assert p["rescue_card_id"] == r.json()["rescue_card_id"]
    await close_socket(b_ws, b_task)


async def test_rescue_card_queued_when_borrower_offline_then_flushed_on_connect(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    _, token = await pair_guardian(client, fake_clock)
    r = await client.post(
        CARD,
        json={"card_type": "CALL_ME_NOW", "message_vi": "Mẹ nghe máy nhé"},
        headers=_auth(token),
    )
    assert r.status_code == 202
    assert r.json()["queued"] is True and r.json()["delivered_via"] == []
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    frame = await b_ws.wait_for("EVENT_RESCUE_CARD")
    assert frame["payload"]["message_vi"] == "Mẹ nghe máy nhé"
    await close_socket(b_ws, b_task)
    # đã xả hàng đợi: lần kết nối sau không còn thẻ
    fake_clock.advance(1)
    b2, t2 = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    assert "EVENT_RESCUE_CARD" not in b2.types()
    await close_socket(b2, t2)


async def test_rescue_card_requires_valid_guardian_session(
    client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    body = {"card_type": "CALL_ME_NOW"}
    assert (await client.post(CARD, json=body)).status_code == 401
    assert (await client.post(CARD, json=body, headers=_auth("gst_not_real"))).status_code == 401


async def test_rescue_card_escalation_must_belong_to_guardians_borrower(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    _, token_a = await pair_guardian(client, fake_clock, device=DEVICE_A)
    await pair_guardian(client, fake_clock, device=DEVICE_B)
    sid_b = await arm_session(client, fake_clock, DEVICE_B, event_id="arm-evt-0000000b")
    esc = await signed_post(
        client,
        fake_clock,
        "/api/v1/shield/escalate",
        escalate_body(sid_b, fake_clock, client_event_id="esc-evt-0000000b"),
        DEVICE_B,
    )
    esc_id = esc.json()["escalation_id"]
    r = await client.post(
        CARD, json={"card_type": "CALL_ME_NOW", "escalation_id": esc_id}, headers=_auth(token_a)
    )
    assert r.status_code == 404
    r2 = await client.post(
        CARD,
        json={"card_type": "CALL_ME_NOW", "escalation_id": str(uuid.uuid4())},
        headers=_auth(token_a),
    )
    assert r2.status_code == 404
