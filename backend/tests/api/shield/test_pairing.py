"""Ghép đôi người thân đồng thuận 2 chiều (S2-EC23, S2-EC24)."""

from __future__ import annotations

import re

import fakeredis
import httpx

from app.core.clock import FakeClock
from app.services.realtime.container import RealtimeServices
from app.services.realtime.models import LinkStatus
from app.services.realtime.pairing import PAIRING_ALPHABET, pair_code_key
from app.services.realtime.repository import InMemoryFamilyRepository

from ._flows import PUSH_SUB, pair_guardian
from ._helpers import DEVICE_A, signed_post

CONFIRM = "/api/v1/family/pair-confirm"


async def _init(client: httpx.AsyncClient, clock: FakeClock) -> dict[str, object]:
    r = await signed_post(
        client, clock, "/api/v1/family/pair-init", {"guardian_alias": "Con trai"}, DEVICE_A
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def test_s2_ec24_pair_init_code_format_and_ttl(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """S2-EC24: mã 6 ký tự entropy cao, TTL 10 phút, tối đa 3 lần sai."""
    data = await _init(client, fake_clock)
    code = str(data["pairing_code"])
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", code)
    assert set(code) <= set(PAIRING_ALPHABET)
    assert data["max_fail_attempts"] == 3
    ttl = await redis.ttl(pair_code_key(code))
    assert 595 <= ttl <= 600
    assert data["qr_payload"] == f"vayshield://pair?code={code}"


async def test_s2_ec24_code_expires_after_10_minutes(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Mã hết hạn (khóa Redis mất sau TTL) → 404 PAIRING_CODE_INVALID."""
    code = str((await _init(client, fake_clock))["pairing_code"])
    await redis.delete(pair_code_key(code))  # mô phỏng hết TTL (fakeredis không theo FakeClock)
    r = await client.post(CONFIRM, json={"pairing_code": code, "consent_accepted": True})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "PAIRING_CODE_INVALID"


async def test_s2_ec24_three_wrong_codes_lock_even_correct_code(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """3 lần sai → 423 PAIRING_LOCKED; sau đó mã đúng cũng bị khóa (chống dò)."""
    code = str((await _init(client, fake_clock))["pairing_code"])
    wrong = "ZZZZZZ" if code != "ZZZZZZ" else "YYYYYY"
    statuses = []
    for _ in range(3):
        r = await client.post(CONFIRM, json={"pairing_code": wrong, "consent_accepted": True})
        statuses.append(r.status_code)
    assert statuses == [404, 404, 423]
    locked = await client.post(CONFIRM, json={"pairing_code": code, "consent_accepted": True})
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "PAIRING_LOCKED"


async def test_s2_ec23_code_single_use_and_consent_required(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    repo: InMemoryFamilyRepository,
) -> None:
    """S2-EC23: đồng thuận 2 chiều — phải consent; mã dùng đúng 1 lần; do người vay khởi tạo."""
    code = str((await _init(client, fake_clock))["pairing_code"])
    no_consent = await client.post(CONFIRM, json={"pairing_code": code, "consent_accepted": False})
    assert no_consent.status_code == 422
    ok = await client.post(
        CONFIRM,
        json={"pairing_code": code, "consent_accepted": True, "webpush_subscription": PUSH_SUB},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ACTIVE"
    assert ok.json()["guardian_session_token"].startswith("gst_")
    again = await client.post(CONFIRM, json={"pairing_code": code, "consent_accepted": True})
    assert again.status_code == 404  # mã đã bị tiêu thụ
    (link,) = repo.links.values()
    assert link.status is LinkStatus.ACTIVE
    assert link.device_id == DEVICE_A


async def test_s2_ec23_pair_init_requires_device_signature(
    client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    r = await client.post("/api/v1/family/pair-init", json={"guardian_alias": "x"})
    assert r.status_code == 401


async def test_s2_ec24_pair_confirm_rate_limited_per_ip(
    client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    """Dò mã nhiều lần từ 1 IP bị rate limit riêng (10/phút) trước khi đụng khóa mã."""
    codes = []
    for i in range(14):
        r = await client.post(
            CONFIRM, json={"pairing_code": f"AAAAA{PAIRING_ALPHABET[i]}", "consent_accepted": True}
        )
        codes.append(r.status_code)
    assert 429 in codes
    assert codes.index(429) <= 10


async def test_pair_confirm_rejects_ssrf_push_endpoint(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """Endpoint Web Push ngoài danh sách dịch vụ push đã biết bị từ chối (chống SSRF)."""
    code = str((await _init(client, fake_clock))["pairing_code"])
    bad = {"endpoint": "https://169.254.169.254/latest/meta-data", "keys": PUSH_SUB["keys"]}
    r = await client.post(
        CONFIRM, json={"pairing_code": code, "consent_accepted": True, "webpush_subscription": bad}
    )
    assert r.status_code == 422


async def test_guardian_session_resolves_after_pairing(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    link_id, token = await pair_guardian(client, fake_clock)
    session = await services.pairing.resolve_session(token)
    assert session.link.link_id == link_id
