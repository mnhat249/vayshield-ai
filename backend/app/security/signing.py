"""Chữ ký Ed25519: gói quy tắc Edge (File 4 §5.1 mục 3) + giờ máy chủ có ký (File 2 §1.1, S2-EC29).

- ``sign_policy_bundle``: ký JSON chuẩn hóa của ``payload`` đúng như contract
  (``json.dumps(payload, sort_keys=True, separators=(",", ":"))``, mặc định ``ensure_ascii``).
- ``signed_epoch_offset``: thiết bị neo TTL vào ``elapsedRealtimeNanos`` + giờ máy chủ có ký, nên
  chỉnh đồng hồ máy (+2 ngày) không phá được Khiên 24h. Thông điệp ký::

      {"device_id": <uuid|null>, "key_id": ..., "purpose": "vayshield.server_epoch.v1",
       "server_time_ms": <int>}   (JSON chuẩn hóa như trên)

- Xác minh sai luôn ném ``SignatureVerificationError`` (không bao giờ trả ``False`` âm thầm).
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.clock import Clock
from app.core.config import Settings, get_settings
from app.core.schemas.devices import SignedEpochOffset
from app.core.schemas.policies import PolicyBundle, PolicyPayload
from app.security.keys import decode_b64_secret

ALGORITHM: Final = "Ed25519"
EPOCH_PURPOSE: Final = "vayshield.server_epoch.v1"
_SEED_LEN: Final = 32
_SIG_LEN: Final = 64


class SignatureVerificationError(Exception):
    """Chữ ký Ed25519 không hợp lệ / sai khóa / sai thuật toán / hết hạn."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Chữ ký không hợp lệ: {reason}")


def canonical_json(obj: Any) -> bytes:
    """JSON chuẩn hóa theo contract: ``sort_keys``, ``separators=(',',':')``."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64decode_sig(value: str) -> bytes:
    raw = value.strip()
    padded = raw + "=" * (-len(raw) % 4)
    try:
        if "-" in raw or "_" in raw:
            sig = base64.urlsafe_b64decode(padded)
        else:
            sig = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureVerificationError("signature_b64 không phải base64") from None
    if len(sig) != _SIG_LEN:
        raise SignatureVerificationError("độ dài chữ ký sai")
    return sig


class Ed25519Signer:
    """Khóa ký Ed25519 của máy chủ. ``repr`` chỉ hiện ``key_id``."""

    __slots__ = ("_private", "key_id")

    def __init__(self, seed: bytes, key_id: str) -> None:
        if len(seed) != _SEED_LEN:
            raise ValueError("seed Ed25519 phải dài 32 byte")
        if not key_id:
            raise ValueError("key_id rỗng")
        self._private = Ed25519PrivateKey.from_private_bytes(seed)
        self.key_id = key_id

    def __repr__(self) -> str:
        return f"Ed25519Signer(key_id={self.key_id!r})"

    __str__ = __repr__

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> Ed25519Signer:
        cfg = settings or get_settings()
        seed = decode_b64_secret(
            cfg.ed25519_policy_private_key, "ED25519_POLICY_PRIVATE_KEY", _SEED_LEN
        )
        return cls(seed, cfg.ed25519_policy_key_id)

    @property
    def public_key_raw(self) -> bytes:
        """32 byte khóa công khai — ghim cứng trong APK/PWA."""
        return self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign(self, message: bytes) -> bytes:
        return self._private.sign(message)


def verify_ed25519(public_key_raw: bytes, message: bytes, signature_b64: str) -> None:
    """Ném ``SignatureVerificationError`` nếu sai; trả ``None`` nếu đúng."""
    sig = _b64decode_sig(signature_b64)
    try:
        key = Ed25519PublicKey.from_public_bytes(public_key_raw)
    except ValueError:
        raise SignatureVerificationError("khóa công khai sai định dạng") from None
    try:
        key.verify(sig, message)
    except InvalidSignature:
        raise SignatureVerificationError("chữ ký không khớp") from None


# ------------------------------------------------------------------ policy ---


def sign_policy_bundle(
    payload: PolicyPayload,
    *,
    policy_version: int,
    issued_at: datetime,
    ttl: timedelta,
    signer: Ed25519Signer,
) -> PolicyBundle:
    if issued_at.tzinfo is None:
        raise ValueError("issued_at phải có tzinfo")
    if ttl <= timedelta(0):
        raise ValueError("ttl phải dương")
    message = canonical_json(payload.model_dump(mode="json"))
    return PolicyBundle(
        policy_version=policy_version,
        issued_at=issued_at,
        expires_at=issued_at + ttl,
        algorithm=ALGORITHM,
        key_id=signer.key_id,
        payload=payload,
        signature_b64=_b64(signer.sign(message)),
    )


def verify_policy_bundle(
    bundle: PolicyBundle,
    *,
    public_key_raw: bytes,
    expected_key_id: str,
    now: datetime | None = None,
    min_policy_version: int | None = None,
) -> None:
    """Kiểm như thiết bị: thuật toán, key_id ghim, chữ ký, hạn, chống hạ cấp phiên bản."""
    if bundle.algorithm != ALGORITHM:
        raise SignatureVerificationError("thuật toán không được phép")
    if bundle.key_id != expected_key_id:
        raise SignatureVerificationError("key_id không khớp khóa ghim")
    verify_ed25519(
        public_key_raw, canonical_json(bundle.payload.model_dump(mode="json")), bundle.signature_b64
    )
    if now is not None and now >= bundle.expires_at:
        raise SignatureVerificationError("gói quy tắc đã hết hạn")
    if min_policy_version is not None and bundle.policy_version < min_policy_version:
        raise SignatureVerificationError("phiên bản gói quy tắc bị hạ cấp")


# ------------------------------------------------------------------- epoch ---


def _epoch_message(server_time_ms: int, key_id: str, device_id: UUID | None) -> bytes:
    return canonical_json(
        {
            "device_id": str(device_id) if device_id is not None else None,
            "key_id": key_id,
            "purpose": EPOCH_PURPOSE,
            "server_time_ms": server_time_ms,
        }
    )


def signed_epoch_offset(
    clock: Clock, signer: Ed25519Signer, *, device_id: UUID | None = None
) -> SignedEpochOffset:
    """Giờ máy chủ (ms UTC) có ký Ed25519 — S2-EC29."""
    server_time_ms = int(clock.now().timestamp() * 1000)
    message = _epoch_message(server_time_ms, signer.key_id, device_id)
    return SignedEpochOffset(
        server_time_ms=server_time_ms,
        key_id=signer.key_id,
        signature_b64=_b64(signer.sign(message)),
    )


def verify_signed_epoch(
    epoch: SignedEpochOffset,
    *,
    public_key_raw: bytes,
    expected_key_id: str,
    device_id: UUID | None = None,
) -> int:
    """Trả ``server_time_ms`` nếu chữ ký hợp lệ; sai → ``SignatureVerificationError``."""
    if epoch.key_id != expected_key_id:
        raise SignatureVerificationError("key_id không khớp khóa ghim")
    verify_ed25519(
        public_key_raw,
        _epoch_message(epoch.server_time_ms, epoch.key_id, device_id),
        epoch.signature_b64,
    )
    return epoch.server_time_ms


def trusted_now_ms(server_time_ms: int, elapsed_at_sync_ms: int, elapsed_now_ms: int) -> int:
    """Giờ tin cậy phía thiết bị = giờ máy chủ có ký + thời gian đơn điệu đã trôi (S2-EC29).

    Không dùng đồng hồ hệ thống: người dùng chỉnh giờ máy không ảnh hưởng kết quả.
    """
    if elapsed_now_ms < elapsed_at_sync_ms:
        raise ValueError("đồng hồ đơn điệu lùi (thiết bị đã khởi động lại) — cần đồng bộ lại")
    return server_time_ms + (elapsed_now_ms - elapsed_at_sync_ms)
