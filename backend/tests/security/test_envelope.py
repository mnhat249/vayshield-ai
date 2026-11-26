"""Envelope AES-256-GCM per-record DEK (File 4 §5.1 mục 1)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import SecretStr

from app.security import envelope
from app.security.envelope import (
    MAGIC,
    EnvelopeCipher,
    EnvelopeDecryptionError,
    record_aad,
)
from app.security.keys import SecurityConfigError

from ._helpers import KEK_A, KEK_B, b64, make_settings

AAD = record_aad("mule_account_nodes", "account_cipher_envelope", uuid.UUID(int=1))


def test_roundtrip_encrypt_decrypt() -> None:
    cipher = EnvelopeCipher(KEK_A)
    blob = cipher.encrypt(b"970436:0123456789", aad=AAD)
    assert blob.startswith(MAGIC)
    assert b"0123456789" not in blob
    assert cipher.decrypt(blob, aad=AAD) == b"970436:0123456789"


def test_roundtrip_empty_plaintext() -> None:
    cipher = EnvelopeCipher(KEK_A)
    assert cipher.decrypt(cipher.encrypt(b"", aad=AAD), aad=AAD) == b""


def test_module_level_helpers_use_settings() -> None:
    cfg = make_settings()
    blob = envelope.encrypt(b"x", aad=AAD, settings=cfg)
    assert envelope.decrypt(blob, aad=AAD, settings=cfg) == b"x"


def test_fresh_dek_and_nonce_per_record() -> None:
    """Cùng plaintext mã hóa 2 lần → khác hoàn toàn (DEK + nonce mới, không tái sử dụng)."""
    cipher = EnvelopeCipher(KEK_A)
    blobs = [cipher.encrypt(b"same", aad=AAD) for _ in range(50)]
    assert len(set(blobs)) == 50
    wrap_nonces = {b[12:24] for b in blobs}
    data_nonces = {b[24 + 48 : 24 + 48 + 12] for b in blobs}
    assert len(wrap_nonces) == 50
    assert len(data_nonces) == 50


def test_aad_binds_blob_to_record() -> None:
    """Chép blob sang bản ghi khác → giải mã thất bại."""
    cipher = EnvelopeCipher(KEK_A)
    blob = cipher.encrypt(b"secret", aad=AAD)
    other = record_aad("mule_account_nodes", "account_cipher_envelope", uuid.UUID(int=2))
    with pytest.raises(EnvelopeDecryptionError):
        cipher.decrypt(blob, aad=other)


def test_aad_required() -> None:
    cipher = EnvelopeCipher(KEK_A)
    with pytest.raises(ValueError):
        cipher.encrypt(b"x", aad=b"")
    with pytest.raises(ValueError):
        record_aad("bad|table", "col", "1")


@pytest.mark.parametrize("pos", [0, 5, 13, 30, 80, -1])
def test_tampered_blob_rejected(pos: int) -> None:
    cipher = EnvelopeCipher(KEK_A)
    blob = bytearray(cipher.encrypt(b"payload-123", aad=AAD))
    blob[pos] ^= 0x01
    with pytest.raises(EnvelopeDecryptionError):
        cipher.decrypt(bytes(blob), aad=AAD)


def test_truncated_blob_rejected() -> None:
    cipher = EnvelopeCipher(KEK_A)
    blob = cipher.encrypt(b"payload", aad=AAD)
    with pytest.raises(EnvelopeDecryptionError):
        cipher.decrypt(blob[:40], aad=AAD)


def test_wrong_kek_rejected_generic_error() -> None:
    blob = EnvelopeCipher(KEK_A).encrypt(b"x", aad=AAD)
    with pytest.raises(EnvelopeDecryptionError) as exc:
        EnvelopeCipher(KEK_B).decrypt(blob, aad=AAD)
    assert "Không giải mã" in str(exc.value)


def test_kek_rotation_prev_still_decrypts_and_rewrap() -> None:
    old = EnvelopeCipher(KEK_A)
    blob = old.encrypt(b"guardian-push-endpoint", aad=AAD)
    rotated = EnvelopeCipher(KEK_B, previous_kek=KEK_A)
    assert rotated.needs_rewrap(blob)
    assert rotated.decrypt(blob, aad=AAD) == b"guardian-push-endpoint"
    new_blob = rotated.rewrap(blob, aad=AAD)
    assert not rotated.needs_rewrap(new_blob)
    assert EnvelopeCipher(KEK_B).decrypt(new_blob, aad=AAD) == b"guardian-push-endpoint"
    # dữ liệu giữ nguyên, chỉ lớp bọc DEK đổi
    assert new_blob[-30:] == blob[-30:]


def test_rewrap_refuses_tampered_ciphertext() -> None:
    rotated = EnvelopeCipher(KEK_B, previous_kek=KEK_A)
    blob = bytearray(EnvelopeCipher(KEK_A).encrypt(b"abc", aad=AAD))
    blob[-1] ^= 0xFF
    with pytest.raises(EnvelopeDecryptionError):
        rotated.rewrap(bytes(blob), aad=AAD)


def test_repr_never_leaks_kek() -> None:
    cipher = EnvelopeCipher(KEK_A, previous_kek=KEK_B)
    text = repr(cipher) + str(cipher)
    for key in (KEK_A, KEK_B):
        assert key.hex() not in text
        assert b64(key) not in text


def test_from_settings_errors_name_only_env_var() -> None:
    with pytest.raises(SecurityConfigError) as exc:
        EnvelopeCipher.from_settings(make_settings(kms_master_kek=None))
    assert "KMS_MASTER_KEK" in str(exc.value)
    bad = "not-base64-!!!"
    with pytest.raises(SecurityConfigError) as exc2:
        EnvelopeCipher.from_settings(make_settings(kms_master_kek=SecretStr(bad)))
    assert bad not in str(exc2.value)
    with pytest.raises(SecurityConfigError):
        EnvelopeCipher.from_settings(make_settings(kms_master_kek=SecretStr(b64(b"short"))))


def test_from_settings_with_prev() -> None:
    blob = EnvelopeCipher(KEK_B).encrypt(b"x", aad=AAD)
    cfg = make_settings(kms_master_kek_prev=SecretStr(b64(KEK_B)))
    assert EnvelopeCipher.from_settings(cfg).decrypt(blob, aad=AAD) == b"x"
