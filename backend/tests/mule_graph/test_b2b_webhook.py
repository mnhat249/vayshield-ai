"""POST /api/v1/b2b/disbursement-shield: S2-EC26, S2-EC43, EC-DB03, EC-DB10, EC-DB19, RLS tenant."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from app.core.clock import FakeClock
from app.core.schemas.common import HsmState
from app.mule_graph.models import DeviceMatch, PartnerRecord, ShieldRecord
from app.mule_graph.repository import InMemoryRepository
from app.security.blind_index import BlindIndexer, BlindIndexKind
from app.shield_state.fibers import EvidenceSource
from app.shield_state.hsm import HsmEvent
from app.shield_state.keys import b2b_idem_key
from app.shield_state.store import ShieldStateStore
from tests.mule_graph.conftest import (
    PARTNER_KEY,
    PHONE_E164,
    SECRET_A,
    SECRET_B,
    body_json,
    sign_headers,
)

URL = "/api/v1/b2b/disbursement-shield"
pytestmark = pytest.mark.usefixtures("b2b_app", "partner_a")


async def _post(
    client: httpx.AsyncClient, clock: FakeClock, raw: bytes, **kw: object
) -> httpx.Response:
    secret = kw.pop("secret", SECRET_A)
    assert isinstance(secret, bytes)
    headers = sign_headers(secret=secret, now=clock.now(), raw=raw, **kw)  # type: ignore[arg-type]
    return await client.post(URL, content=raw, headers=headers)


def _now_ms(clock: FakeClock) -> int:
    return int(clock.now().timestamp() * 1000)


async def test_valid_webhook_arms_shield_on_device(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    store: ShieldStateStore,
) -> None:
    resp = await _post(client, fake_clock, body_json(phone_hmac))
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "ARMED_ON_DEVICE"
    assert data["pre_simo_active_wave_warning"] == {
        "impersonation_wave_active": False,
        "active_clusters_targeting_brand": 0,
    }
    snap = await store.load(str(device_id), _now_ms(fake_clock))
    assert snap.state is HsmState.S4_SHIELD_ARMED_24H
    assert snap.shield_expires_at_ms == _now_ms(fake_clock) + 24 * 3_600_000


async def test_wrong_signature_rejected(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str
) -> None:
    raw = body_json(phone_hmac)
    headers = sign_headers(secret=SECRET_A, now=fake_clock.now(), raw=raw)
    headers["X-VayShield-Signature"] = "sha256=" + "0" * 64
    resp = await client.post(URL, content=raw, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SIGNATURE_INVALID"


async def test_signature_made_with_other_partner_secret_rejected(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str
) -> None:
    resp = await _post(client, fake_clock, body_json(phone_hmac), secret=SECRET_B)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SIGNATURE_INVALID"


async def test_tampered_body_rejected(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str
) -> None:
    raw = body_json(phone_hmac)
    headers = sign_headers(secret=SECRET_A, now=fake_clock.now(), raw=raw)
    tampered = body_json(phone_hmac, amount_bucket_tier="TIER_UNDER_3M")
    resp = await client.post(URL, content=tampered, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SIGNATURE_INVALID"


async def test_unknown_partner_key_rejected_like_bad_signature(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str
) -> None:
    resp = await _post(client, fake_clock, body_json(phone_hmac), partner_key="vsp_unknown_x")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SIGNATURE_INVALID"


async def test_inactive_partner_rejected(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    repo: InMemoryRepository,
) -> None:
    repo.partners[PARTNER_KEY] = PartnerRecord(id=uuid4(), hmac_secret=SECRET_A, is_active=False)
    resp = await _post(client, fake_clock, body_json(phone_hmac))
    assert resp.status_code == 401


async def test_missing_auth_headers_rejected(client: httpx.AsyncClient, phone_hmac: str) -> None:
    resp = await client.post(
        URL, content=body_json(phone_hmac), headers={"Idempotency-Key": "k" * 10}
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("skew", [45, -45, 299, -299])
async def test_ec_db19_clock_skew_within_300s_window_accepted(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str, skew: int
) -> None:
    """EC-DB19: lệch giờ NTP 45s (và tới 299s) vẫn nằm trong cửa sổ 300s."""
    resp = await _post(client, fake_clock, body_json(phone_hmac), skew_s=skew)
    assert resp.status_code == 202, resp.text


@pytest.mark.parametrize("skew", [301, -301, 3600])
async def test_ec_db19_timestamp_outside_window_rejected(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str, skew: int
) -> None:
    resp = await _post(client, fake_clock, body_json(phone_hmac), skew_s=skew)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TIMESTAMP_OUT_OF_WINDOW"


async def test_replay_after_window_is_rejected_even_with_valid_signature(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str
) -> None:
    """S2-EC26: gói tin bị bắt giữ rồi phát lại sau 300s thất bại."""
    raw = body_json(phone_hmac)
    headers = sign_headers(secret=SECRET_A, now=fake_clock.now(), raw=raw)
    first = await client.post(URL, content=raw, headers=headers)
    assert first.status_code == 202
    fake_clock.advance(301)
    replay = await client.post(URL, content=raw, headers=headers)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "TIMESTAMP_OUT_OF_WINDOW"


async def test_s2_ec26_duplicate_idempotency_key_returns_same_shield_and_arms_once(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    store: ShieldStateStore,
    repo: InMemoryRepository,
) -> None:
    raw = body_json(phone_hmac)
    first = await _post(client, fake_clock, raw)
    v1 = (await store.load(str(device_id), _now_ms(fake_clock))).version
    fake_clock.advance(10)
    second = await _post(client, fake_clock, raw)
    v2 = (await store.load(str(device_id), _now_ms(fake_clock))).version
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert v1 == v2, "retry không được ghi lại trạng thái Khiên"
    assert len(repo.shields) == 1


async def test_s2_ec26_retry_storm_concurrent_creates_single_shield(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    repo: InMemoryRepository,
) -> None:
    raw = body_json(phone_hmac)
    results = await asyncio.gather(*(_post(client, fake_clock, raw) for _ in range(8)))
    statuses = [r.status_code for r in results]
    ok = [r.json()["shield_id"] for r in results if r.status_code == 202]
    assert len(repo.shields) == 1
    assert len(set(ok)) == 1 and ok, statuses
    assert all(s in (202, 409) for s in statuses)


async def test_s2_ec26_same_key_different_body_conflict(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str, device_id: UUID
) -> None:
    first = await _post(client, fake_clock, body_json(phone_hmac))
    other = await _post(client, fake_clock, body_json(phone_hmac, amount_bucket_tier="TIER_3M_10M"))
    assert first.status_code == 202
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_s2_ec26_idempotency_key_ttl_is_48h(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    partner_a: PartnerRecord,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    resp = await _post(client, fake_clock, body_json(phone_hmac), key="idem-ttl-0001")
    assert resp.status_code == 202
    ttl = await redis.pttl(b2b_idem_key(str(partner_a.id), "idem-ttl-0001"))
    assert 47 * 3_600_000 < ttl <= 48 * 3_600_000


async def test_s2_ec43_b2b_and_edge_notification_merge_into_one_fiber(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    store: ShieldStateStore,
) -> None:
    """S2-EC43: thông báo trên máy tới trước, webhook B2B ngay sau → 1 luồng, gộp nguồn, MAX hạn."""
    now_ms = _now_ms(fake_clock)
    await store.arm_shield(
        str(device_id),
        "default",
        HsmEvent.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=now_ms,
        event_id="edge-1",
        source=EvidenceSource.EDGE_NOTIF,
    )
    fake_clock.advance(1)
    resp = await _post(client, fake_clock, body_json(phone_hmac, shield_duration_hours=48))
    assert resp.status_code == 202
    assert resp.json()["status"] == "ARMED_ON_DEVICE"
    snap = await store.load(str(device_id), _now_ms(fake_clock))
    assert len(snap.fibers) == 1
    fiber = snap.fibers[0]
    assert {EvidenceSource.B2B_CONFIRMED, EvidenceSource.EDGE_NOTIF} <= set(fiber.evidence_sources)
    assert snap.shield_expires_at_ms == _now_ms(fake_clock) + 48 * 3_600_000
    disb = [e for e in fiber.evidence if e.kind.value == "DISBURSEMENT"]
    assert len(disb) == 1, "không cộng 2 lần w_disb"


async def test_ec_db03_expired_phone_optin_does_not_arm_recycled_number(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    repo: InMemoryRepository,
    store: ShieldStateStore,
) -> None:
    """EC-DB03: opt-in quá 90 ngày chưa gia hạn → không bắn Khiên vào máy chủ cũ."""
    old_owner = uuid4()
    repo.devices_by_phone[phone_hmac] = DeviceMatch(
        device_id=old_owner, phone_optin_exp_at=fake_clock.now()
    )
    resp = await _post(client, fake_clock, body_json(phone_hmac))
    assert resp.status_code == 202
    assert resp.json()["status"] == "NO_DEVICE_OPTED_IN"
    snap = await store.load(str(old_owner), _now_ms(fake_clock))
    assert snap.fibers == ()


async def test_ec_db03_unlinked_phone_hmac_removed_means_no_shield(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str
) -> None:
    resp = await _post(client, fake_clock, body_json(phone_hmac))
    assert resp.status_code == 202
    assert resp.json()["status"] == "NO_DEVICE_OPTED_IN"


@pytest.mark.parametrize(
    "variant", ["0909123456", "+84909123456", "84909123456", "+84 0909 123 456"]
)
async def test_ec_db10_phone_formats_normalize_to_same_blind_index_and_match(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    indexer: BlindIndexer,
    device_id: UUID,
    variant: str,
) -> None:
    """EC-DB10: mọi biểu diễn SĐT → cùng blind index → khớp cùng thiết bị."""
    h = indexer.index(BlindIndexKind.PHONE, variant)
    assert h == indexer.index(BlindIndexKind.PHONE, PHONE_E164)
    resp = await _post(client, fake_clock, body_json(h))
    assert resp.json()["status"] == "ARMED_ON_DEVICE"


async def test_rls_tenant_isolation_same_idempotency_key_different_partners(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    partner_a: PartnerRecord,
    partner_b: PartnerRecord,
    repo: InMemoryRepository,
) -> None:
    """Tenant A không thấy/không đụng shield của tenant B dù trùng Idempotency-Key (EC-DB15)."""
    raw = body_json(phone_hmac)
    a = await _post(client, fake_clock, raw, key="shared-key-01")
    b = await _post(
        client,
        fake_clock,
        raw,
        key="shared-key-01",
        secret=SECRET_B,
        partner_key="vsp_test_partnerB",
    )
    assert a.status_code == b.status_code == 202
    assert a.json()["shield_id"] != b.json()["shield_id"]
    own_a = await repo.get_shield(partner_a.id, "shared-key-01")
    own_b = await repo.get_shield(partner_b.id, "shared-key-01")
    assert own_a is not None and own_b is not None and own_a.id != own_b.id
    assert await repo.get_shield(uuid4(), "shared-key-01") is None


async def test_rate_limit_per_partner_returns_429(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    repo: InMemoryRepository,
) -> None:
    repo.partners[PARTNER_KEY] = PartnerRecord(id=uuid4(), hmac_secret=SECRET_A, rate_limit_rpm=2)
    codes = [
        (await _post(client, fake_clock, body_json(phone_hmac), key=f"idem-rl-{i:04d}")).status_code
        for i in range(4)
    ]
    assert codes[:2] == [202, 202]
    assert 429 in codes[2:]


async def test_store_degraded_marks_pending_user_online(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    app: FastAPI,
) -> None:
    from app.api.routes.b2b import get_shield_store
    from app.core.errors import AppError, ErrorCode

    class DownStore(ShieldStateStore):
        async def arm_shield(self, *a: object, **k: object):  # type: ignore[no-untyped-def]
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)

    app.dependency_overrides[get_shield_store] = lambda: DownStore(fakeredis.FakeAsyncRedis())
    resp = await _post(client, fake_clock, body_json(phone_hmac))
    assert resp.status_code == 202
    assert resp.json()["status"] == "PENDING_USER_ONLINE"


async def test_invalid_body_is_422(client: httpx.AsyncClient, fake_clock: FakeClock) -> None:
    resp = await _post(client, fake_clock, b'{"borrower_phone_blind_hmac":"x"}')
    assert resp.status_code == 422


async def test_webhook_latency_under_70ms_with_fakes(
    client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str, device_id: UUID
) -> None:
    """DoD: webhook < 70ms (fake repo + fakeredis). Lấy trung vị 15 lần để tránh nhiễu CI."""
    await _post(client, fake_clock, body_json(phone_hmac), key="warmup-0001")
    samples: list[float] = []
    for i in range(15):
        raw = body_json(phone_hmac)
        t0 = time.perf_counter()
        resp = await _post(client, fake_clock, raw, key=f"lat-key-{i:04d}")
        samples.append((time.perf_counter() - t0) * 1000)
        assert resp.status_code == 202
    samples.sort()
    assert samples[len(samples) // 2] < 70, samples


async def test_default_repository_not_configured_is_503(
    app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock, phone_hmac: str
) -> None:
    from app.api.routes.b2b import get_b2b_repository

    app.dependency_overrides.pop(get_b2b_repository, None)
    resp = await _post(client, fake_clock, body_json(phone_hmac))
    assert resp.status_code == 503


async def test_review_m1_expired_redis_key_same_idem_key_different_body_is_409_and_not_armed(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    partner_a: PartnerRecord,
    repo: InMemoryRepository,
    indexer: BlindIndexer,
    store: ShieldStateStore,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Review M1: khóa Redis hết hạn/mất -> DB (UNIQUE) vẫn chặn; body khác -> 409, không bật
    Khiên cho SĐT/thiết bị của body mới."""
    first = await _post(client, fake_clock, body_json(phone_hmac), key="idem-m1-0001")
    assert first.status_code == 202
    await redis.delete(b2b_idem_key(str(partner_a.id), "idem-m1-0001"))  # TTL 48h hết / degraded

    other_phone = indexer.index(BlindIndexKind.PHONE, "+84912000111")
    other_dev = uuid4()
    repo.devices_by_phone[other_phone] = DeviceMatch(
        device_id=other_dev, phone_optin_exp_at=fake_clock.now() + timedelta(days=30)
    )
    for raw in (
        body_json(phone_hmac, amount_bucket_tier="TIER_3M_10M"),
        body_json(other_phone),  # SĐT khác
    ):
        resp = await _post(client, fake_clock, raw, key="idem-m1-0001")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        await redis.delete(b2b_idem_key(str(partner_a.id), "idem-m1-0001"))
    snap = await store.load(str(other_dev), _now_ms(fake_clock))
    assert snap.fibers == (), "body mới không được bật Khiên trên thiết bị khác"
    assert len(repo.shields) == 1


async def test_review_m1_expired_redis_key_same_body_replays_same_shield_without_rearming(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    partner_a: PartnerRecord,
    store: ShieldStateStore,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    raw = body_json(phone_hmac)
    first = await _post(client, fake_clock, raw, key="idem-m1-0002")
    v1 = (await store.load(str(device_id), _now_ms(fake_clock))).version
    await redis.delete(b2b_idem_key(str(partner_a.id), "idem-m1-0002"))
    again = await _post(client, fake_clock, raw, key="idem-m1-0002")
    assert again.status_code == 202 and again.json() == first.json()
    assert (await store.load(str(device_id), _now_ms(fake_clock))).version == v1


async def test_review_m1_race_branch_compares_against_request_not_itself(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    device_id: UUID,
    partner_a: PartnerRecord,
    repo: InMemoryRepository,
    redis: fakeredis.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nhánh created=False (đua DB) phải so nội dung với yêu cầu hiện tại -> 409 nếu khác."""
    first = await _post(client, fake_clock, body_json(phone_hmac), key="idem-m1-0003")
    assert first.status_code == 202
    await redis.delete(b2b_idem_key(str(partner_a.id), "idem-m1-0003"))
    real_get = repo.get_shield
    calls = 0

    async def blind_first_get(partner_id: UUID, key: str) -> ShieldRecord | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_get(partner_id, key)  # pre-check "chưa thấy"

    monkeypatch.setattr(repo, "get_shield", blind_first_get)
    resp = await _post(
        client,
        fake_clock,
        body_json(phone_hmac, amount_bucket_tier="TIER_3M_10M"),
        key="idem-m1-0003",
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_review_minor_ip_rate_limit_runs_before_partner_lookup(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    repo: InMemoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import b2b as b2b_routes
    from app.security.rate_limit import CompositeRateLimiter

    monkeypatch.setattr(
        b2b_routes,
        "_ip_limiter",
        lambda r: CompositeRateLimiter(r, device_rpm=2, ip_rpm=2),
    )
    calls = 0
    real = repo.get_partner_by_key

    async def counting(key: str) -> PartnerRecord | None:
        nonlocal calls
        calls += 1
        return await real(key)

    monkeypatch.setattr(repo, "get_partner_by_key", counting)
    codes = [
        (
            await _post(
                client,
                fake_clock,
                body_json(phone_hmac),
                partner_key="vsp_unknown_xxxx",
                key=f"ip-lim-{i:04d}",
            )
        ).status_code
        for i in range(4)
    ]
    assert codes[:2] == [401, 401] and codes[2:] == [429, 429]
    assert calls == 2, "yêu cầu bị chặn bởi IP không được chạm DB đối tác"


async def test_review_minor_unknown_key_runs_dummy_hmac_and_returns_signature_invalid(
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    phone_hmac: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.routes import b2b as b2b_routes

    seen: list[object] = []
    from app.security.partner_hmac import verify_partner_request as real

    def spy(**kw: Any) -> int:
        seen.append(kw["secret"])
        return real(**kw)

    monkeypatch.setattr("app.api.routes.b2b.verify_partner_request", spy)
    resp = await _post(client, fake_clock, body_json(phone_hmac), partner_key="vsp_unknown_xxxx")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "SIGNATURE_INVALID"
    assert seen == [b2b_routes._DUMMY_SECRET]
