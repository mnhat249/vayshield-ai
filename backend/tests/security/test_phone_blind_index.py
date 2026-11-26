"""Chuẩn hóa E.164 (EC-DB10) + blind index dual-pepper (EC-DB08)."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from pydantic import SecretStr

from app.security.blind_index import (
    BlindIndexer,
    BlindIndexInputError,
    BlindIndexKind,
    blind_index,
    blind_index_lookup,
    normalize_mule_account,
)
from app.security.keys import SecurityConfigError
from app.security.phone import PhoneNormalizationError, normalize_phone_e164

from ._helpers import (
    PEPPER_MULE_V1,
    PEPPER_MULE_V2,
    PEPPER_PHONE_V1,
    PEPPER_PHONE_V2,
    make_settings,
)

# ------------------------------------------------------------- EC-DB10 ---


@pytest.mark.parametrize(
    "raw",
    [
        "0909123456",
        "+84909123456",
        "84909123456",
        "840909123456",
        "+840909123456",
        "+84 (0)909.123.456",
        "0909 123 456",
        "0909-123-456",
        "0084909123456",
        " +84909123456 ",
    ],
)
def test_ec_db10_vn_formats_normalize_to_same_e164(raw: str) -> None:
    assert normalize_phone_e164(raw) == "+84909123456"


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ("01662123456", "+84362123456"),  # Viettel 0166 → 036
        ("+841662123456", "+84362123456"),
        ("01212345678", "+84792345678"),  # MobiFone 0121 → 079
        ("01232345678", "+84832345678"),  # VinaPhone 0123 → 083
    ],
)
def test_ec_db10_legacy_11_digit_prefix_mapped(legacy: str, expected: str) -> None:
    assert normalize_phone_e164(legacy) == expected


def test_ec_db10_international_kept() -> None:
    assert normalize_phone_e164("+14155550123") == "+14155550123"


@pytest.mark.parametrize(
    "raw", ["", "abc", "090912345", "09091234567", "0209123456", "909123456", "+0909", "1" * 20]
)
def test_ec_db10_invalid_rejected_without_echoing_input(raw: str) -> None:
    with pytest.raises(PhoneNormalizationError) as exc:
        normalize_phone_e164(raw)
    if raw:
        assert raw not in str(exc.value)


def test_ec_db10_hmac_identical_across_formats_and_matches_partner_vector() -> None:
    """Test vector chung cho SDK đối tác: HMAC_SHA256(pepper, '+84909123456')."""
    cfg = make_settings()
    expected = hmac.new(PEPPER_PHONE_V1.encode(), b"+84909123456", hashlib.sha256).hexdigest()
    values = {
        blind_index(BlindIndexKind.PHONE, v, settings=cfg)
        for v in ("0909123456", "+84909123456", "840909123456")
    }
    assert values == {expected}
    assert len(expected) == 64


# ------------------------------------------------------------- EC-DB08 ---


def test_ec_db08_rotation_old_hash_still_found_and_lazy_rehash() -> None:
    old_cfg = make_settings()
    stored_v1 = blind_index(BlindIndexKind.PHONE, "0909123456", settings=old_cfg)

    rotated = make_settings(
        pepper_b2b_phone=SecretStr(PEPPER_PHONE_V2),
        pepper_b2b_phone_prev=SecretStr(PEPPER_PHONE_V1),
        pepper_b2b_phone_version=2,
    )
    lookup = blind_index_lookup(BlindIndexKind.PHONE, "+84909123456", settings=rotated)
    assert lookup.current_version == 2
    assert lookup.previous_version == 1
    assert stored_v1 in lookup.candidates  # WHERE col IN (v2, v1) vẫn tra được
    assert lookup.candidates[0] == lookup.current != stored_v1
    assert lookup.matches(stored_v1)
    assert lookup.needs_rehash(stored_v1, 1)
    assert not lookup.needs_rehash(lookup.current, 2)
    assert not lookup.matches("0" * 64)


def test_ec_db08_rotation_mule_account() -> None:
    stored_v1 = blind_index(
        BlindIndexKind.MULE_ACCOUNT, "970436:0123 456 789", settings=make_settings()
    )
    rotated = make_settings(
        pepper_mule_account=SecretStr(PEPPER_MULE_V2),
        pepper_mule_account_prev=SecretStr(PEPPER_MULE_V1),
        pepper_mule_account_version=2,
    )
    lookup = blind_index_lookup(BlindIndexKind.MULE_ACCOUNT, "970436:0123456789", settings=rotated)
    assert stored_v1 in lookup.candidates
    assert lookup.needs_rehash(stored_v1, 1)


def test_ec_db08_without_prev_single_candidate() -> None:
    lookup = blind_index_lookup(BlindIndexKind.PHONE, "0909123456", settings=make_settings())
    assert lookup.candidates == (lookup.current,)
    assert lookup.previous is None


def test_ec_db08_prev_requires_version_bump() -> None:
    cfg = make_settings(
        pepper_b2b_phone=SecretStr(PEPPER_PHONE_V2),
        pepper_b2b_phone_prev=SecretStr(PEPPER_PHONE_V1),
        pepper_b2b_phone_version=1,
    )
    with pytest.raises(SecurityConfigError):
        BlindIndexer.from_settings(cfg)


def test_peppers_are_independent_per_kind() -> None:
    cfg = make_settings(pepper_mule_account=SecretStr(PEPPER_PHONE_V1))
    phone = blind_index(BlindIndexKind.PHONE, "0909123456", settings=make_settings())
    assert phone == blind_index(BlindIndexKind.PHONE, "0909123456", settings=cfg)
    mule = blind_index(BlindIndexKind.MULE_ACCOUNT, "970436:0909123456", settings=make_settings())
    assert mule != phone


def test_missing_or_short_pepper_errors_name_only() -> None:
    with pytest.raises(SecurityConfigError) as exc:
        blind_index(
            BlindIndexKind.PHONE, "0909123456", settings=make_settings(pepper_b2b_phone=None)
        )
    assert "PHONE" in str(exc.value)
    with pytest.raises(SecurityConfigError) as exc2:
        BlindIndexer.from_settings(make_settings(pepper_b2b_phone=SecretStr("short-pepper")))
    assert "short-pepper" not in str(exc2.value)
    assert "PEPPER_B2B_PHONE" in str(exc2.value)


def test_mule_account_normalization() -> None:
    assert normalize_mule_account("970436", " 0123.456-789 ") == "970436:0123456789"
    assert normalize_mule_account("970422", "vqrabc1234") == "970422:VQRABC1234"
    with pytest.raises(BlindIndexInputError):
        normalize_mule_account("97x", "0123456789")
    with pytest.raises(BlindIndexInputError):
        normalize_mule_account("970436", "12")
    with pytest.raises(BlindIndexInputError):
        blind_index(BlindIndexKind.MULE_ACCOUNT, "0123456789", settings=make_settings())


def test_indexer_repr_hides_peppers() -> None:
    indexer = BlindIndexer.from_settings(
        make_settings(pepper_b2b_phone_prev=SecretStr(PEPPER_PHONE_V2), pepper_b2b_phone_version=2)
    )
    text = repr(indexer)
    for p in (PEPPER_PHONE_V1, PEPPER_PHONE_V2, PEPPER_MULE_V1):
        assert p not in text
