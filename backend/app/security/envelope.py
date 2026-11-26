"""Envelope encryption AES-256-GCM: KEK ngoài DB + DEK ngẫu nhiên riêng từng bản ghi (File 4 §5.1).

Định dạng blob v1 (lưu vào cột BYTEA, vd ``mule_account_nodes.account_cipher_envelope``,
``family_guardian_links.webpush_subscription_cipher``)::

    MAGIC "VSE1" (4) | kek_id (8) | wrap_nonce (12) | wrapped_dek (32+16) | data_nonce (12) | ct+tag

- DEK 32 byte sinh mới cho MỖI lần mã hóa; nonce 96-bit ngẫu nhiên (``os.urandom``) cho cả lớp
  bọc DEK lẫn lớp dữ liệu → không bao giờ tái sử dụng cặp (key, nonce).
- AAD bắt buộc, gắn với bản ghi (``record_aad(table, column, record_id)``): chép blob sang bản ghi
  khác sẽ giải mã thất bại.
- ``kek_id`` = HMAC(KEK, nhãn)[:8] — chọn KEK hiện tại hoặc ``KMS_MASTER_KEK_PREV`` khi xoay khóa;
  ``rewrap`` bọc lại DEK bằng KEK mới mà không đụng tới dữ liệu.
- Lỗi giải mã luôn là ``EnvelopeDecryptionError`` chung chung (không phân biệt nguyên nhân).
"""

from __future__ import annotations

import os
from typing import Final
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import Settings, get_settings
from app.security.keys import decode_b64_secret, key_fingerprint, wipe

MAGIC: Final = b"VSE1"
_KEK_ID_LEN: Final = 8
_NONCE_LEN: Final = 12
_DEK_LEN: Final = 32
_TAG_LEN: Final = 16
_WRAPPED_DEK_LEN: Final = _DEK_LEN + _TAG_LEN
_HEADER_LEN: Final = len(MAGIC) + _KEK_ID_LEN
_MIN_BLOB_LEN: Final = _HEADER_LEN + _NONCE_LEN + _WRAPPED_DEK_LEN + _NONCE_LEN + _TAG_LEN
_KEK_LABEL: Final = b"vayshield.envelope.kek-id.v1"
_WRAP_LABEL: Final = b"vayshield.envelope.wrap.v1|"
_DATA_LABEL: Final = b"vayshield.envelope.data.v1|"


class EnvelopeDecryptionError(Exception):
    """Không giải mã được (sai KEK, sai AAD, blob bị sửa hoặc hỏng)."""

    def __init__(self) -> None:
        super().__init__("Không giải mã được dữ liệu niêm phong")


def record_aad(table: str, column: str, record_id: UUID | str) -> bytes:
    """AAD chuẩn gắn blob với đúng 1 ô dữ liệu (bảng, cột, khóa chính)."""
    for part in (table, column):
        if not part or "|" in part:
            raise ValueError("table/column không hợp lệ cho AAD")
    if isinstance(record_id, UUID):
        rid = str(record_id)
    else:
        try:  # chuẩn hóa UUID dạng chữ hoa/không gạch → cùng AAD
            rid = str(UUID(record_id))
        except ValueError:
            rid = record_id
    if not rid or "|" in rid:
        raise ValueError("record_id không hợp lệ cho AAD")
    return f"{table}|{column}|{rid}".encode()


class EnvelopeCipher:
    """Mã hóa/giải mã envelope. Không lộ khóa qua ``repr``/``str``."""

    __slots__ = ("_current", "_current_id", "_previous")

    def __init__(self, current_kek: bytes, previous_kek: bytes | None = None) -> None:
        if len(current_kek) != _DEK_LEN or (
            previous_kek is not None and len(previous_kek) != _DEK_LEN
        ):
            raise ValueError("KEK phải dài 32 byte (AES-256)")
        self._current = AESGCM(current_kek)
        self._current_id = key_fingerprint(current_kek, _KEK_LABEL, _KEK_ID_LEN)
        self._previous: tuple[bytes, AESGCM] | None = None
        if previous_kek is not None:
            prev_id = key_fingerprint(previous_kek, _KEK_LABEL, _KEK_ID_LEN)
            if prev_id != self._current_id:
                self._previous = (prev_id, AESGCM(previous_kek))

    def __repr__(self) -> str:
        has_prev = self._previous is not None
        return f"EnvelopeCipher(kek_id={self._current_id.hex()}, has_previous={has_prev})"

    __str__ = __repr__

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> EnvelopeCipher:
        cfg = settings or get_settings()
        current = decode_b64_secret(cfg.kms_master_kek, "KMS_MASTER_KEK", _DEK_LEN)
        previous = (
            decode_b64_secret(cfg.kms_master_kek_prev, "KMS_MASTER_KEK_PREV", _DEK_LEN)
            if cfg.kms_master_kek_prev is not None
            else None
        )
        return cls(current, previous)

    @property
    def current_kek_id(self) -> bytes:
        return self._current_id

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> bytes:
        if not aad:
            raise ValueError("AAD bắt buộc (gắn blob với bản ghi)")
        header = MAGIC + self._current_id
        dek = bytearray(AESGCM.generate_key(bit_length=256))
        try:
            data_nonce = os.urandom(_NONCE_LEN)
            ciphertext = AESGCM(bytes(dek)).encrypt(data_nonce, plaintext, _DATA_LABEL + aad)
            wrap_nonce = os.urandom(_NONCE_LEN)
            wrapped = self._current.encrypt(wrap_nonce, bytes(dek), _WRAP_LABEL + header + aad)
        finally:
            wipe(dek)
        return header + wrap_nonce + wrapped + data_nonce + ciphertext

    def _parse(self, blob: bytes) -> tuple[bytes, AESGCM, bytes, bytes, bytes, bytes]:
        if len(blob) < _MIN_BLOB_LEN or blob[: len(MAGIC)] != MAGIC:
            raise EnvelopeDecryptionError
        header = blob[:_HEADER_LEN]
        kek_id = blob[len(MAGIC) : _HEADER_LEN]
        if kek_id == self._current_id:
            kek = self._current
        elif self._previous is not None and kek_id == self._previous[0]:
            kek = self._previous[1]
        else:
            raise EnvelopeDecryptionError
        pos = _HEADER_LEN
        wrap_nonce = blob[pos : pos + _NONCE_LEN]
        pos += _NONCE_LEN
        wrapped = blob[pos : pos + _WRAPPED_DEK_LEN]
        pos += _WRAPPED_DEK_LEN
        data_nonce = blob[pos : pos + _NONCE_LEN]
        pos += _NONCE_LEN
        return header, kek, wrap_nonce, wrapped, data_nonce, blob[pos:]

    def _unwrap(
        self, header: bytes, kek: AESGCM, nonce: bytes, wrapped: bytes, aad: bytes
    ) -> bytearray:
        try:
            return bytearray(kek.decrypt(nonce, wrapped, _WRAP_LABEL + header + aad))
        except InvalidTag:
            raise EnvelopeDecryptionError from None

    def decrypt(self, blob: bytes, *, aad: bytes) -> bytes:
        if not aad:
            raise ValueError("AAD bắt buộc (gắn blob với bản ghi)")
        header, kek, wrap_nonce, wrapped, data_nonce, ciphertext = self._parse(bytes(blob))
        dek = self._unwrap(header, kek, wrap_nonce, wrapped, aad)
        try:
            return AESGCM(bytes(dek)).decrypt(data_nonce, ciphertext, _DATA_LABEL + aad)
        except InvalidTag:
            raise EnvelopeDecryptionError from None
        finally:
            wipe(dek)

    def needs_rewrap(self, blob: bytes) -> bool:
        """True nếu blob đang bọc bằng KEK cũ (cần ``rewrap`` sau khi xoay KEK)."""
        return bytes(blob[len(MAGIC) : _HEADER_LEN]) != self._current_id

    def rewrap(self, blob: bytes, *, aad: bytes) -> bytes:
        """Bọc lại DEK bằng KEK hiện tại; dữ liệu (data_nonce + ciphertext) giữ nguyên."""
        if not aad:
            raise ValueError("AAD bắt buộc (gắn blob với bản ghi)")
        header, kek, wrap_nonce, wrapped, data_nonce, ciphertext = self._parse(bytes(blob))
        dek = self._unwrap(header, kek, wrap_nonce, wrapped, aad)
        try:
            # Kiểm tra toàn vẹn dữ liệu trước khi bọc lại (không bọc lại blob hỏng).
            try:
                AESGCM(bytes(dek)).decrypt(data_nonce, ciphertext, _DATA_LABEL + aad)
            except InvalidTag:
                raise EnvelopeDecryptionError from None
            new_header = MAGIC + self._current_id
            new_nonce = os.urandom(_NONCE_LEN)
            new_wrapped = self._current.encrypt(
                new_nonce, bytes(dek), _WRAP_LABEL + new_header + aad
            )
        finally:
            wipe(dek)
        return new_header + new_nonce + new_wrapped + data_nonce + ciphertext


def get_envelope_cipher(settings: Settings | None = None) -> EnvelopeCipher:
    """FastAPI dependency/helper. Thiếu ``KMS_MASTER_KEK`` → ``SecurityConfigError``."""
    return EnvelopeCipher.from_settings(settings)


def encrypt(plaintext: bytes, *, aad: bytes, settings: Settings | None = None) -> bytes:
    return get_envelope_cipher(settings).encrypt(plaintext, aad=aad)


def decrypt(blob: bytes, *, aad: bytes, settings: Settings | None = None) -> bytes:
    return get_envelope_cipher(settings).decrypt(blob, aad=aad)
