"""POST /devices/register, DELETE /devices/me, GET /policies/latest (+ readiness registry)."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.clock import FakeClock
from app.core.schemas.policies import PolicyBundle
from app.security.signing import Ed25519Signer, verify_policy_bundle, verify_signed_epoch
from app.services.audit_pipeline.apk import ApkTrustEntry, ApkTrustStore
from app.services.audit_pipeline.container import UnconfiguredRegistry
from app.services.audit_pipeline.registration import leading_zero_bits
from app.verdict import InMemoryLenderRegistry
from app.verdict.registry import LenderRecord
from tests.api.shield._helpers import DEVICE_A, H, sign
from tests.security._helpers import make_settings

REGISTER = "/api/v1/devices/register"
POLICY = "/api/v1/policies/latest"


def _spki(seed: int = 4242) -> str:
    key = ec.derive_private_key(seed, ec.SECP256R1())
    der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


def _reg(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "public_key_spki_b64": _spki(),
        "platform_type": "ANDROID_NATIVE",
        "app_version": "1.0.0",
        "consents": {"contract_scan": True, "shield_monitoring": True},
    }
    body.update(over)
    return body


def _mine(spki: str, ts: int, bits: int) -> str:
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{spki}:{ts}:{nonce}".encode()).digest()
        if leading_zero_bits(digest) >= bits:
            return str(nonce)
        nonce += 1


async def test_register_returns_signed_epoch_and_stores_only_public_key(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await client.post(REGISTER, json=_reg(daily_wage_vnd=400_000))
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["daily_wage_vnd"] == 400_000 and data["policy_version"] >= 1
    device_id = UUID(data["device_id"])
    stored = await env.services.devices.get(device_id)
    assert stored is not None and stored.platform_type == "ANDROID_NATIVE"
    signer = Ed25519Signer.from_settings(make_settings())
    from app.core.schemas.devices import SignedEpochOffset

    ms = verify_signed_epoch(
        SignedEpochOffset.model_validate(data["server_epoch"]),
        public_key_raw=signer.public_key_raw,
        expected_key_id=signer.key_id,
        device_id=device_id,
    )
    assert ms == int(fake_clock.now().timestamp() * 1000)


async def test_register_rejects_invalid_public_key(env: Any, client: httpx.AsyncClient) -> None:
    for bad in ("A" * 60, base64.b64encode(b"x" * 70).decode()):
        r = await client.post(REGISTER, json=_reg(public_key_spki_b64=bad))
        assert r.status_code == 422, r.text
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_ec_db25_register_pow_valid_and_invalid(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock, audit_settings: Any
) -> None:
    cfg = audit_settings.model_copy(update={"pow_difficulty_bits": 8})
    build_env(settings=cfg)
    spki = _spki()
    ts = int(fake_clock.now().timestamp())
    ok = await client.post(
        REGISTER,
        json=_reg(
            public_key_spki_b64=spki,
            proof_of_work={"timestamp": ts, "nonce": _mine(spki, ts, 8)},
        ),
    )
    assert ok.status_code == 201, ok.text
    bad = await client.post(
        REGISTER,
        json=_reg(
            public_key_spki_b64=spki, proof_of_work={"timestamp": ts, "nonce": "definitely-wrong"}
        ),
    )
    assert bad.status_code == 400
    stale = await client.post(
        REGISTER,
        json=_reg(
            public_key_spki_b64=spki,
            proof_of_work={"timestamp": ts - 10_000, "nonce": _mine(spki, ts - 10_000, 8)},
        ),
    )
    assert stale.status_code == 400


async def test_ec_db25_register_without_pow_is_rate_limited_per_ip(
    env: Any, client: httpx.AsyncClient
) -> None:
    codes = [
        (await client.post(REGISTER, json=_reg(public_key_spki_b64=_spki(9000 + i)))).status_code
        for i in range(12)
    ]
    assert codes[:10] == [201] * 10
    assert codes[10:] == [429, 429]


async def test_ec_db03_ec_db10_phone_stored_as_blind_hmac_and_never_hijacked(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """SĐT chỉ lưu HMAC (đã chuẩn hóa E.164); SĐT đã thuộc thiết bị khác KHÔNG bị chiếm; opt-in
    hết hạn sau 90 ngày."""
    first = await client.post(REGISTER, json=_reg(phone_e164_optin="+84909123456"))
    second = await client.post(
        REGISTER,
        json=_reg(public_key_spki_b64=_spki(777), phone_e164_optin="+840909123456"),
    )
    d1, d2 = first.json(), second.json()
    exp = d1["phone_optin_expires_at"]
    assert exp is not None
    # Cùng dạng phản hồi (không làm oracle dò SĐT) nhưng KHÔNG chiếm liên kết SĐT:
    assert d2["phone_optin_expires_at"] == exp
    dev1 = await env.services.devices.get(UUID(d1["device_id"]))
    assert dev1 is not None and dev1.phone_blind_hmac is not None
    assert "909123456" not in dev1.phone_blind_hmac and len(dev1.phone_blind_hmac) == 64
    assert dev1.phone_optin_exp_at == fake_clock.now() + timedelta(days=90)
    dev2 = await env.services.devices.get(UUID(d2["device_id"]))
    assert dev2 is not None and dev2.phone_blind_hmac is None
    bad = await client.post(REGISTER, json=_reg(phone_e164_optin="+84123"))
    assert bad.status_code == 422


async def test_ec_db21_delete_me_erases_device(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    from tests.api.shield._helpers import PUBKEYS

    await env.services.devices.create(
        __import__("app.services.audit_pipeline.repository", fromlist=["x"]).DeviceRecord(
            device_id=DEVICE_A,
            pubkey_spki=PUBKEYS[DEVICE_A],
            platform_type="WEB_PWA",
            daily_wage_vnd=320_000,
            policy_version=1,
        )
    )
    path = "/api/v1/devices/me"
    headers = sign(fake_clock, DEVICE_A, "DELETE", path, b"")
    r = await client.delete(path, headers={H: headers[H]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "WIPED"
    assert await env.services.devices.get(DEVICE_A) is None
    assert (await client.delete(path)).status_code == 401


# ---------------------------------------------------------------- policies ---


async def _get_policy(
    client: httpx.AsyncClient, clock: FakeClock, **headers: str
) -> httpx.Response:
    signed = sign(clock, DEVICE_A, "GET", POLICY, b"")
    return await client.get(POLICY, headers={H: signed[H], **headers})


async def test_policy_bundle_is_ed25519_signed_with_etag_and_304(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    trust = ApkTrustStore(
        {"vn.homecredit.demo": ApkTrustEntry("FINCO_HOMECREDIT", frozenset({"ab" * 32}))}
    )
    build_env(trust=trust)
    r = await _get_policy(client, fake_clock)
    assert r.status_code == 200, r.text
    etag = r.headers["etag"]
    assert etag.startswith('"policy-v')
    bundle = PolicyBundle.model_validate(r.json())
    signer = Ed25519Signer.from_settings(make_settings())
    verify_policy_bundle(
        bundle,
        public_key_raw=signer.public_key_raw,
        expected_key_id=signer.key_id,
        now=fake_clock.now(),
    )
    codes = {w.entity_code for w in bundle.payload.lender_whitelist}
    assert "FINCO_HOMECREDIT" in codes
    home = next(w for w in bundle.payload.lender_whitelist if w.entity_code == "FINCO_HOMECREDIT")
    assert any(p.sha256 == "ab" * 32 for p in home.android_packages)
    assert ["android.permission.READ_CONTACTS", "android.permission.READ_MEDIA_IMAGES"] in (
        bundle.payload.dangerous_permission_combos
    )
    assert bundle.payload.thresholds.cooling_off_seconds == 15

    fake_clock.advance(2)
    not_modified = await _get_policy(client, fake_clock, **{"If-None-Match": etag})
    assert not_modified.status_code == 304 and not_modified.content == b""
    assert not_modified.headers["etag"] == etag
    fake_clock.advance(2)  # cùng giây = bị coi là phát lại (chống replay của T10)
    weak = await _get_policy(client, fake_clock, **{"If-None-Match": f'W/{etag}, "other"'})
    assert weak.status_code == 304
    fake_clock.advance(2)
    changed = await _get_policy(client, fake_clock, **{"If-None-Match": '"policy-v1-stale"'})
    assert changed.status_code == 200


async def test_policy_etag_changes_when_registry_changes(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock, registry: Any
) -> None:
    e = build_env()
    first = (await _get_policy(client, fake_clock)).headers["etag"]
    fake_clock.advance(2)
    extra = LenderRecord(
        entity_code="FINCO_TEST_NEW",
        legal_category=registry.get("FINCO_HOMECREDIT").legal_category,
        official_short_name="Test New",
        official_legal_name="Cong ty Test New",
    )
    e.services.registry = InMemoryLenderRegistry([*registry.active_records(), extra])
    second = (await _get_policy(client, fake_clock)).headers["etag"]
    assert first != second


async def test_policy_without_signing_key_is_503_not_unsigned(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    e = build_env()
    e.services.signer = None
    r = await _get_policy(client, fake_clock)
    assert r.status_code == 503 and r.json()["error"]["code"] == "SERVICE_DEGRADED"


async def test_policy_requires_auth(env: Any, client: httpx.AsyncClient) -> None:
    assert (await client.get(POLICY)).status_code == 401


# --------------------------------------------------------------- readiness ---


class _LazyRegistry:
    """Giống ``PgLenderRegistry``: chưa ``refresh()`` thì mọi truy vấn raise."""

    def __init__(self, records: Sequence[LenderRecord]) -> None:
        self._records = records
        self.loaded = False

    def refresh(self) -> None:
        self.loaded = True

    def active_records(self) -> Sequence[LenderRecord]:
        if not self.loaded:
            raise RuntimeError("not loaded")
        return self._records

    def get(self, entity_code: str) -> LenderRecord | None:
        return next((r for r in self.active_records() if r.entity_code == entity_code), None)


async def test_registry_not_loaded_fails_closed_then_loads_lazily(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock, registry: Any
) -> None:
    """T09: registry rỗng sẽ tắt tín hiệu mạo danh → chưa nạp = 503 (không phục vụ), nạp được
    (refresh lười ở request đầu) thì phục vụ bình thường."""
    lazy = _LazyRegistry(list(registry.active_records()))
    attempts = {"n": 0}

    async def refresh() -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ConnectionError("postgres down")
        lazy.refresh()

    e = build_env(registry=lazy)
    e.services.refresher = refresh
    assert e.services.readiness()["lender_registry"] is False
    down = await _get_policy(client, fake_clock)
    assert down.status_code == 503 and down.json()["error"]["code"] == "SERVICE_DEGRADED"
    fake_clock.advance(2)
    up = await _get_policy(client, fake_clock)
    assert up.status_code == 200
    assert e.services.readiness()["lender_registry"] is True


async def test_no_database_means_registry_unconfigured_not_empty(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    e = build_env(registry=UnconfiguredRegistry())
    r = await _get_policy(client, fake_clock)
    assert r.status_code == 503
    assert e.services.readiness()["lender_registry"] is False


async def test_pow_is_single_use_and_public_key_is_unique(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock, audit_settings: Any
) -> None:
    """Review MAJOR 5: PoW gửi lại không tạo thêm thiết bị; 1 khóa công khai = 1 thiết bị."""
    cfg = audit_settings.model_copy(update={"pow_difficulty_bits": 8})
    build_env(settings=cfg)
    spki = _spki(31337)
    ts = int(fake_clock.now().timestamp())
    pow_ = {"timestamp": ts, "nonce": _mine(spki, ts, 8)}
    ok = await client.post(REGISTER, json=_reg(public_key_spki_b64=spki, proof_of_work=pow_))
    assert ok.status_code == 201
    replay = await client.post(REGISTER, json=_reg(public_key_spki_b64=spki, proof_of_work=pow_))
    assert replay.status_code == 400
    assert replay.json()["error"]["details"][0]["issue"] == "already_used"
    # Cùng khóa, PoW mới (không dùng lại) → vẫn bị từ chối vì khóa đã đăng ký.
    ts2 = ts + 1
    again = await client.post(
        REGISTER,
        json=_reg(
            public_key_spki_b64=spki,
            proof_of_work={"timestamp": ts2, "nonce": _mine(spki, ts2, 8)},
        ),
    )
    assert again.status_code == 409
