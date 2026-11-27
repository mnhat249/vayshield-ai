"""Tiện ích test Trạm 3: ký request thiết bị, socket giả, hẹn giờ theo đồng hồ giả."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from starlette.websockets import WebSocketDisconnect

from app.core.clock import FakeClock
from app.security.device_auth import canonical_device_message

DEVICE_A = uuid.UUID("019361a0-0000-7000-8000-00000000a001")
DEVICE_B = uuid.UUID("019361a0-0000-7000-8000-00000000b002")
_KEYS = {
    DEVICE_A: ec.derive_private_key(1111111, ec.SECP256R1()),
    DEVICE_B: ec.derive_private_key(2222222, ec.SECP256R1()),
}
PUBKEYS = {
    d: k.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    for d, k in _KEYS.items()
}
H = "X-VayShield-Device-Signature"


async def key_resolver(device_id: uuid.UUID) -> bytes | None:
    return PUBKEYS.get(device_id)


def sign(
    clock: FakeClock, device: uuid.UUID, method: str, path: str, body: bytes
) -> dict[str, str]:
    ts = int(clock.now().timestamp())
    msg = canonical_device_message(method, path, ts, body)
    sig = _KEYS[device].sign(msg, ec.ECDSA(hashes.SHA256()))
    b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return {
        H: f"v1;device_id={device};ts={ts};sig={b64}",
        "Content-Type": "application/json",
    }


async def signed_post(
    client: httpx.AsyncClient,
    clock: FakeClock,
    path: str,
    payload: dict[str, Any],
    device: uuid.UUID = DEVICE_A,
) -> httpx.Response:
    body = json.dumps(payload).encode()
    return await client.post(path, content=body, headers=sign(clock, device, "POST", path, body))


async def signed_get(
    client: httpx.AsyncClient, clock: FakeClock, path: str, device: uuid.UUID = DEVICE_A
) -> httpx.Response:
    return await client.get(path, headers=sign(clock, device, "GET", path, b""))


class ManualSleeper:
    """``sleep`` gắn với ``FakeClock``: chỉ tỉnh khi ``advance`` đẩy đồng hồ qua hạn."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self._waiters: list[tuple[float, asyncio.Future[None]]] = []
        self.requested: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.requested.append(seconds)
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append((self._clock.monotonic() + seconds, fut))
        await fut

    async def advance(self, seconds: float) -> None:
        self._clock.advance(seconds)
        now = self._clock.monotonic()
        due = [w for w in self._waiters if w[0] <= now]
        self._waiters = [w for w in self._waiters if w[0] > now]
        for _, fut in due:
            if not fut.done():
                fut.set_result(None)
        for _ in range(20):  # nhường vòng lặp cho các task vừa tỉnh chạy hết
            await asyncio.sleep(0)


class FakeWebSocket:
    """Socket giả đủ cho handler: accept/send_json/receive_text/close (cùng event loop với test)."""

    def __init__(self, ticket: str | None, client_ip: str = "10.0.0.1") -> None:
        self.query = ticket
        self.client = type("C", (), {"host": client_ip, "port": 1234})()
        self.sent: list[dict[str, Any]] = []
        self.inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.close_code: int | None = None
        self.accepted = False
        self._sent_event = asyncio.Event()

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)
        self._sent_event.set()

    async def receive_text(self) -> str:
        item = await self.inbox.get()
        if item is None:
            raise WebSocketDisconnect(1000)
        return item

    async def close(self, code: int = 1000) -> None:
        self.close_code = code
        await self.inbox.put(None)

    def client_send(self, frame_type: str, payload: dict[str, Any]) -> None:
        frame = {
            "type": frame_type,
            "frame_id": str(uuid.uuid4()),
            "sent_at": "2026-11-25T01:00:00+00:00",
            "payload": payload,
        }
        self.inbox.put_nowait(json.dumps(frame))

    def disconnect(self) -> None:
        self.inbox.put_nowait(None)

    def types(self) -> list[str]:
        return [f["type"] for f in self.sent]

    async def wait_for(self, frame_type: str, wait_s: float = 2.0) -> dict[str, Any]:
        async def _poll() -> dict[str, Any]:
            while True:
                for f in self.sent:
                    if f["type"] == frame_type:
                        return f
                self._sent_event.clear()
                await self._sent_event.wait()

        return await asyncio.wait_for(_poll(), wait_s)


async def until(predicate: Callable[[], Awaitable[bool] | bool], wait_s: float = 2.0) -> None:
    async def _loop() -> None:
        while True:
            res = predicate()
            if (await res) if isinstance(res, Awaitable) else res:
                return
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_loop(), wait_s)
