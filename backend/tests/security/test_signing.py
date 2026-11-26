"""Ed25519 policy bundle (File 4 §5.1 mục 3) + SignedServerEpochOffset (S2-EC29)."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.clock import FakeClock
from app.core.schemas.policies import PolicyPayload, PolicyThresholds
from app.security.keys import SecurityConfigError
from app.security.signing import (
    Ed25519Signer,
    SignatureVerificationError,
    canonical_json,
    sign_policy_bundle,
    signed_epoch_offset,
    trusted_now_ms,
    verify_policy_bundle,
    verify_signed_epoch,
)

from ._helpers import ED25519_SEED, b64, make_settings

KEY_ID = "policy-test-k1"
NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)


def _payload(**kw: object) -> PolicyPayload:
    base: dict[str, object] = {
        "lender_whitelist": [],
        "banking_app_packages": ["com.vnpay.bidv", "com.mbmobile"],
        "dangerous_permission_combos": [["BIND_ACCESSIBILITY_SERVICE", "SYSTEM_ALERT_WINDOW"]],
        "blacklist_bloom_b64": None,
        "thresholds": PolicyThresholds(
            p_scam_watch=0.35,
            p_scam_cooling_off=0.7,
            cooling_off_seconds=15,
            shield_duration_hours=24,
            smurf_window_minutes=15,
        ),
        "challenge_templates": [],
    }
    base.update(kw)
    return PolicyPayload(**base)  # type: ignore[arg-type]


@pytest.fixture
def signer() -> Ed25519Signer:
    return Ed25519Signer(ED25519_SEED, KEY_ID)


def test_sign_and_verify_policy_bundle(signer: Ed25519Signer) -> None:
    bundle = sign_policy_bundle(
        _payload(), policy_version=3, issued_at=NOW, ttl=timedelta(days=7), signer=signer
    )
    assert bundle.algorithm == "Ed25519"
    assert bundle.key_id == KEY_ID
    assert bundle.expires_at == NOW + timedelta(days=7)
    verify_policy_bundle(bundle, public_key_raw=signer.public_key_raw, expected_key_id=KEY_ID)


def test_signature_is_over_contract_canonical_json(signer: Ed25519Signer) -> None:
    """Thiết bị tái tạo đúng: json.dumps(payload, sort_keys=True, separators=(',',':'))."""
    bundle = sign_policy_bundle(
        _payload(), policy_version=1, issued_at=NOW, ttl=timedelta(days=1), signer=signer
    )
    wire = json.loads(bundle.model_dump_json())
    msg = json.dumps(wire["payload"], sort_keys=True, separators=(",", ":")).encode()
    pub = Ed25519PrivateKey.from_private_bytes(ED25519_SEED).public_key()
    pub.verify(base64.b64decode(wire["signature_b64"]), msg)  # không ném = khớp
    assert msg == canonical_json(bundle.payload.model_dump(mode="json"))


def test_tampered_payload_rejected_explicitly(signer: Ed25519Signer) -> None:
    """MITM hạ cấp bộ lọc (bỏ gói ngân hàng) → lỗi tường minh."""
    bundle = sign_policy_bundle(
        _payload(), policy_version=1, issued_at=NOW, ttl=timedelta(days=1), signer=signer
    )
    forged = bundle.model_copy(update={"payload": _payload(banking_app_packages=[])})
    with pytest.raises(SignatureVerificationError) as exc:
        verify_policy_bundle(forged, public_key_raw=signer.public_key_raw, expected_key_id=KEY_ID)
    assert exc.value.reason == "chữ ký không khớp"


def test_wrong_key_wrong_kid_bad_b64_expired_downgrade(signer: Ed25519Signer) -> None:
    bundle = sign_policy_bundle(
        _payload(), policy_version=2, issued_at=NOW, ttl=timedelta(hours=1), signer=signer
    )
    other = Ed25519Signer(bytes([9]) * 32, KEY_ID)
    pub = signer.public_key_raw
    with pytest.raises(SignatureVerificationError):
        verify_policy_bundle(bundle, public_key_raw=other.public_key_raw, expected_key_id=KEY_ID)
    with pytest.raises(SignatureVerificationError):
        verify_policy_bundle(bundle, public_key_raw=pub, expected_key_id="other-kid")
    with pytest.raises(SignatureVerificationError):
        verify_policy_bundle(
            bundle.model_copy(update={"signature_b64": "!!!"}),
            public_key_raw=pub,
            expected_key_id=KEY_ID,
        )
    with pytest.raises(SignatureVerificationError):
        verify_policy_bundle(
            bundle.model_copy(update={"signature_b64": b64(b"\x00" * 10)}),
            public_key_raw=pub,
            expected_key_id=KEY_ID,
        )
    with pytest.raises(SignatureVerificationError):
        verify_policy_bundle(
            bundle, public_key_raw=pub, expected_key_id=KEY_ID, now=NOW + timedelta(hours=2)
        )
    with pytest.raises(SignatureVerificationError):
        verify_policy_bundle(
            bundle, public_key_raw=pub, expected_key_id=KEY_ID, min_policy_version=3
        )
    with pytest.raises(SignatureVerificationError):
        verify_policy_bundle(bundle, public_key_raw=b"\x01" * 5, expected_key_id=KEY_ID)


def test_signer_from_settings_and_repr_hides_seed() -> None:
    s = Ed25519Signer.from_settings(make_settings())
    assert s.key_id == "policy-test-k1"
    assert len(s.public_key_raw) == 32
    assert ED25519_SEED.hex() not in repr(s)
    assert b64(ED25519_SEED) not in repr(s)
    with pytest.raises(SecurityConfigError) as exc:
        Ed25519Signer.from_settings(make_settings(ed25519_policy_private_key=None))
    assert "ED25519_POLICY_PRIVATE_KEY" in str(exc.value)


# ---------------------------------------------------------------- S2-EC29 ---


def test_s2_ec29_signed_epoch_roundtrip(signer: Ed25519Signer) -> None:
    clock = FakeClock(NOW)
    dev = uuid.UUID("019361a0-0000-7000-8000-000000000001")
    epoch = signed_epoch_offset(clock, signer, device_id=dev)
    assert epoch.server_time_ms == int(NOW.timestamp() * 1000)
    got = verify_signed_epoch(
        epoch, public_key_raw=signer.public_key_raw, expected_key_id=KEY_ID, device_id=dev
    )
    assert got == epoch.server_time_ms


def test_s2_ec29_forged_server_time_rejected(signer: Ed25519Signer) -> None:
    """Kẻ gian sửa server_time_ms (+2 ngày) để Khiên 24h hết hạn sớm → chữ ký sai."""
    epoch = signed_epoch_offset(FakeClock(NOW), signer)
    forged = epoch.model_copy(update={"server_time_ms": epoch.server_time_ms + 2 * 86_400_000})
    with pytest.raises(SignatureVerificationError):
        verify_signed_epoch(forged, public_key_raw=signer.public_key_raw, expected_key_id=KEY_ID)


def test_s2_ec29_epoch_bound_to_device(signer: Ed25519Signer) -> None:
    dev_a, dev_b = uuid.UUID(int=1), uuid.UUID(int=2)
    epoch = signed_epoch_offset(FakeClock(NOW), signer, device_id=dev_a)
    with pytest.raises(SignatureVerificationError):
        verify_signed_epoch(
            epoch, public_key_raw=signer.public_key_raw, expected_key_id=KEY_ID, device_id=dev_b
        )


def test_s2_ec29_trusted_time_ignores_wall_clock_changes() -> None:
    """Giờ tin cậy = server_time + elapsedRealtime đã trôi; đổi giờ máy không ảnh hưởng."""
    server_ms = int(NOW.timestamp() * 1000)
    assert trusted_now_ms(server_ms, 5_000, 5_000 + 3_600_000) == server_ms + 3_600_000
    with pytest.raises(ValueError):
        trusted_now_ms(server_ms, 10_000, 1_000)  # reboot → phải đồng bộ lại
