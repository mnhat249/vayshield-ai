"""Xác thực chữ ký ECDSA P-256 của thiết bị ẩn danh (File 4 §5.1 mục 4).

Header (đã khóa trong contract, xem ``app/core/auth_headers.py``)::

    X-VayShield-Device-Signature: v1;device_id=<uuid>;ts=<unix_s>;sig=<base64url>
    sig = ECDSA_P256_SHA256(device_key,
          METHOD "\\n" PATH?QUERY "\\n" ts "\\n" hex(sha256(raw_body)))

- Chữ ký nhận DER (Android Keystore) hoặc raw ``r||s`` 64 byte (WebCrypto PWA).
- Khóa công khai ở ``anonymous_devices.pubkey_ecdsa_p256``: điểm không nén 65 byte hoặc DER SPKI.
- ``|now - ts| <= 300s`` (chịu lệch đồng hồ ~45s, cùng quy tắc EC-DB19) + chống phát lại:
  ``SET NX`` khóa (device_id, sha256(thông điệp)) trong 2× cửa sổ — không băm chữ ký (ECDSA dễ
  uốn). Thiếu Redis ngoài LOCAL/TEST → 503 (fail-closed).
- Retry (mạng lỗi, 5xx, 429) PHẢI ký lại với ``ts`` mới: gửi lại nguyên header cũ sẽ bị coi là
  phát lại → 401.
- Mọi lỗi → 401 JSON; không tiết lộ thiết bị có tồn tại hay không. Không log header/chữ ký.

Route dùng: ``device: Annotated[VerifiedDevice, Depends(verify_device_request)]`` (dependency này
dựa trên ``DeviceCaller`` có sẵn nên không thêm tham số OpenAPI mới).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Final
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.serialization import load_der_public_key
from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy import text

from app.core import redis as core_redis
from app.core.auth_headers import DeviceCaller
from app.core.clock import Clock, get_clock
from app.core.config import AppEnv, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger

logger = get_logger("vayshield.security.device_auth")

SIGNATURE_WINDOW_S: Final = 300
REPLAY_KEY_PREFIX: Final = "vs:sig:dev:"
_HEADER_RE: Final = re.compile(
    r"v1;device_id=(?P<device_id>[0-9a-fA-F-]{36});ts=(?P<ts>\d{1,12});sig=(?P<sig>[A-Za-z0-9_\-+/=]{16,200})"
)
_MAX_SIG_BYTES: Final = 80

DeviceKeyResolver = Callable[[UUID], Awaitable[bytes | None]]


@dataclass(frozen=True, slots=True)
class ParsedDeviceSignature:
    device_id: UUID
    ts: int
    signature: bytes  # đã chuẩn hóa về DER

    def __repr__(self) -> str:  # không lộ chữ ký
        return f"ParsedDeviceSignature(device_id={self.device_id}, ts={self.ts})"


@dataclass(frozen=True, slots=True)
class VerifiedDevice:
    device_id: UUID
    signed_at: int


def _fail(code: ErrorCode = ErrorCode.SIGNATURE_INVALID) -> AppError:
    return AppError(code, 401)


def _b64url_decode(value: str) -> bytes:
    raw = value.replace("+", "-").replace("/", "_").rstrip("=")
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        raise _fail() from None


def _is_der(sig: bytes) -> bool:
    if not sig or sig[0] != 0x30 or len(sig) > _MAX_SIG_BYTES:
        return False
    try:
        decode_dss_signature(sig)
    except ValueError:
        return False
    return True


def _to_der(sig: bytes) -> bytes:
    """DER (Android Keystore) được ưu tiên nhận diện trước; 64 byte không phải DER → P1363."""
    if _is_der(sig):
        return sig
    if len(sig) == 64:  # IEEE P1363 r||s (WebCrypto)
        r = int.from_bytes(sig[:32], "big")
        s = int.from_bytes(sig[32:], "big")
        return encode_dss_signature(r, s)
    raise _fail()


def parse_device_signature_header(value: str) -> ParsedDeviceSignature:
    match = _HEADER_RE.fullmatch(value.strip())
    if match is None:
        raise _fail(ErrorCode.UNAUTHORIZED)
    try:
        device_id = UUID(match["device_id"])
    except ValueError:
        raise _fail(ErrorCode.UNAUTHORIZED) from None
    return ParsedDeviceSignature(
        device_id=device_id,
        ts=int(match["ts"]),
        signature=_to_der(_b64url_decode(match["sig"])),
    )


def canonical_device_message(method: str, path_and_query: str, ts: int, body: bytes) -> bytes:
    return "\n".join(
        (method.upper(), path_and_query, str(ts), hashlib.sha256(body).hexdigest())
    ).encode("utf-8")


def load_device_public_key(raw: bytes) -> ec.EllipticCurvePublicKey:
    """Điểm không nén 65 byte hoặc DER SubjectPublicKeyInfo, bắt buộc P-256."""
    try:
        if len(raw) == 65 and raw[0] == 0x04:
            return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        key = load_der_public_key(raw)
    except (ValueError, TypeError):
        raise _fail() from None
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise _fail()
    return key


def verify_device_signature(
    public_key_raw: bytes, parsed: ParsedDeviceSignature, message: bytes
) -> None:
    key = load_device_public_key(public_key_raw)
    try:
        key.verify(parsed.signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise _fail() from None


def check_timestamp_window(ts: int, now_s: int, window_s: int = SIGNATURE_WINDOW_S) -> None:
    """``|now - ts| <= window`` (EC-DB19: 300s đủ cho lệch NTP ~45s)."""
    if abs(now_s - ts) > window_s:
        raise _fail(ErrorCode.TIMESTAMP_OUT_OF_WINDOW)


def replay_key(device_id: UUID, message: bytes) -> str:
    """Khóa chống phát lại theo (thiết bị, thông điệp chuẩn hóa) — KHÔNG theo byte chữ ký.

    ECDSA có tính dễ uốn (``(r, s)`` và ``(r, n - s)`` cùng hợp lệ, DER/P1363 cùng nghĩa), nên băm
    chữ ký sẽ để kẻ tấn công đổi ``s`` mà phát lại được. Thông điệp đã gồm method, path, ts và
    sha256(body), nên 2 request giống hệt nhau trong cùng 1 giây bị coi là phát lại (client phải
    chờ ts mới — giả định chấp nhận được).
    """
    digest = hashlib.sha256(message).hexdigest()
    return f"{REPLAY_KEY_PREFIX}{device_id}:{digest}"


async def mark_request_used(redis: Redis, device_id: UUID, message: bytes, ttl_s: int) -> None:
    """Chống phát lại: cùng (thiết bị, thông điệp) chỉ được chấp nhận 1 lần trong cửa sổ."""
    try:
        first = await redis.set(replay_key(device_id, message), "1", nx=True, ex=ttl_s)
    except Exception:
        logger.warning("device_auth: replay cache Redis lỗi — từ chối (fail-closed)")
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503) from None
    if not first:
        raise _fail()


def request_path_and_query(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    path = raw_path.decode("latin-1") if isinstance(raw_path, bytes) else request.url.path
    query = request.scope.get("query_string", b"")
    if isinstance(query, bytes) and query:
        return f"{path}?{query.decode('latin-1')}"
    return path


async def db_device_key_resolver(device_id: UUID) -> bytes | None:
    """Đọc ``pubkey_ecdsa_p256`` bằng session ngắn (không giữ kết nối suốt request)."""
    from app.core.db import short_session

    async with short_session() as session:
        result = await session.execute(
            text("SELECT pubkey_ecdsa_p256 FROM anonymous_devices WHERE id = :id"),
            {"id": device_id},
        )
        value = result.scalar_one_or_none()
    return bytes(value) if value is not None else None


def get_device_key_resolver() -> DeviceKeyResolver:
    """Dependency; test override bằng resolver trong bộ nhớ."""
    return db_device_key_resolver


async def verify_device_request(
    request: Request,
    caller: DeviceCaller,
    clock: Annotated[Clock, Depends(get_clock)],
    resolver: Annotated[DeviceKeyResolver, Depends(get_device_key_resolver)],
) -> VerifiedDevice:
    """FastAPI dependency: kiểm chữ ký thiết bị đầy đủ; sai → 401."""
    if not caller.device_signature:
        raise _fail(ErrorCode.UNAUTHORIZED)
    parsed = parse_device_signature_header(caller.device_signature)
    check_timestamp_window(parsed.ts, int(clock.now().timestamp()))
    public_key = await resolver(parsed.device_id)
    if public_key is None:
        raise _fail()
    body = await request.body()
    message = canonical_device_message(
        request.method, request_path_and_query(request), parsed.ts, body
    )
    verify_device_signature(public_key, parsed, message)
    redis = core_redis.init_redis()
    if redis is None:
        if get_settings().app_env not in (AppEnv.LOCAL, AppEnv.TEST):
            logger.warning("device_auth: thiếu Redis cho chống phát lại — từ chối (fail-closed)")
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
    else:
        await mark_request_used(redis, parsed.device_id, message, 2 * SIGNATURE_WINDOW_S)
    return VerifiedDevice(device_id=parsed.device_id, signed_at=parsed.ts)


VerifiedDeviceCaller = Annotated[VerifiedDevice, Depends(verify_device_request)]
