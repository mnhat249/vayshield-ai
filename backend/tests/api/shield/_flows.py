"""Luồng dùng lại giữa các test: ghép đôi người thân đầy đủ qua API."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx

from app.api.routes.ws import guardian_ws
from app.core.clock import FakeClock
from app.services.realtime.container import RealtimeServices

from ._helpers import DEVICE_A, FakeWebSocket, signed_get, signed_post

PUSH_ENDPOINT = "https://fcm.googleapis.com/fcm/send/abcdef123456"
PUSH_SUB: dict[str, Any] = {
    "endpoint": PUSH_ENDPOINT,
    "keys": {"p256dh": "BPubKeyPubKeyPubKey", "auth": "authsecret123"},
}
SMS_CONTACT: dict[str, str] = {"channel": "SMS", "value": "0901234567"}
_UNSET: Any = object()


async def pair_guardian(
    client: httpx.AsyncClient,
    clock: FakeClock,
    *,
    device: uuid.UUID = DEVICE_A,
    alias: str = "Con trai",
    label: str | None = "Mẹ",
    webpush: dict[str, Any] | None = _UNSET,
    fallback: dict[str, str] | None = _UNSET,
) -> tuple[uuid.UUID, str]:
    """Ghép đôi đầy đủ qua API; trả (link_id, guardian_session_token)."""
    init = await signed_post(
        client,
        clock,
        "/api/v1/family/pair-init",
        {"guardian_alias": alias, "borrower_display_label": label},
        device,
    )
    assert init.status_code == 201, init.text
    body: dict[str, Any] = {
        "pairing_code": init.json()["pairing_code"],
        "consent_accepted": True,
    }
    sub = PUSH_SUB if webpush is _UNSET else webpush
    contact = SMS_CONTACT if fallback is _UNSET else fallback
    if sub is not None:
        body["webpush_subscription"] = sub
    if contact is not None:
        body["fallback_contact"] = contact
    confirm = await client.post("/api/v1/family/pair-confirm", json=body)
    assert confirm.status_code == 200, confirm.text
    data = confirm.json()
    return uuid.UUID(data["link_id"]), data["guardian_session_token"]


async def arm_session(
    client: httpx.AsyncClient,
    clock: FakeClock,
    device: uuid.UUID = DEVICE_A,
    event_id: str = "arm-evt-00000001",
) -> uuid.UUID:
    r = await signed_post(
        client,
        clock,
        "/api/v1/shield/arm",
        {
            "fiber_key": "default",
            "trigger_origin": "STATION_3_NOTIF",
            "amount_bucket_tier": "TIER_3M_10M",
            "client_event_id": event_id,
        },
        device,
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["session_id"])


def escalate_body(session_id: uuid.UUID, clock: FakeClock, **over: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session_id": str(session_id),
        "reason": "COERCION_DETECTED",
        "hsm_state": "S6_COOLING_OFF_LOCK",
        "detected_axes": ["C1"],
        "client_event_id": "esc-evt-00000001",
        "occurred_at": clock.now().isoformat(),
    }
    body.update(over)
    return body


async def borrower_ticket(client: httpx.AsyncClient, clock: FakeClock, device: uuid.UUID) -> str:
    r = await signed_get(client, clock, "/api/v1/ws-ticket", device)
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "BORROWER"
    return str(r.json()["ticket"])


async def guardian_ticket(client: httpx.AsyncClient, token: str) -> str:
    r = await client.get("/api/v1/ws-ticket", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "GUARDIAN"
    return str(r.json()["ticket"])


async def open_socket(
    services: RealtimeServices, ticket: str | None
) -> tuple[FakeWebSocket, asyncio.Task[None]]:
    ws = FakeWebSocket(ticket)
    task = asyncio.create_task(guardian_ws(ws, services, ticket))  # type: ignore[arg-type]
    await ws.wait_for("SERVER_HELLO")
    return ws, task


async def close_socket(ws: FakeWebSocket, task: asyncio.Task[None]) -> None:
    ws.disconnect()
    await asyncio.wait_for(task, 2)
