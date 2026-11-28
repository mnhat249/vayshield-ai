"""Đăng ký thiết bị ẩn danh (File 4 §4.1 < 80ms, §5.1, §5.2; EC-DB03, EC-DB10, EC-DB25).

- Khóa công khai: DER SPKI P-256 hợp lệ (``load_device_public_key``) — sai → 422 (không phải 401).
- Hashcash (``ProofOfWork``): ``sha256(spki_b64 ':' timestamp ':' nonce)`` đủ
  ``pow_difficulty_bits`` bit 0 đầu, timestamp trong ±300s. Có PoW hợp lệ → hạn mức đăng ký/IP
  cao hơn; không PoW → hạn mức thấp (rate limit; giả định: PoW tùy chọn theo contract).
- GIẢ ĐỊNH: mất phản hồi rồi retry cùng khóa nhận 409 (không trả lại device cũ vì chưa chứng
  minh sở hữu khóa); client tạo khóa mới. CẦN migration mới: UNIQUE INDEX trên
  ``anonymous_devices.pubkey_ecdsa_p256`` + ON CONFLICT (hiện còn race SELECT→INSERT).
- SĐT opt-in: chuẩn hóa E.164 (EC-DB10) → blind index HMAC dual-pepper; chỉ lưu HMAC. SĐT đã
  thuộc thiết bị khác → KHÔNG chiếm (chưa xác minh quyền sở hữu), thiết bị vẫn đăng ký nhưng
  không liên kết SĐT. Opt-in hết hạn sau 90 ngày (EC-DB03).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.errors import AppError, ErrorCode, ErrorDetail
from app.core.ids import new_uuid7
from app.core.logging import get_logger
from app.core.schemas.devices import DeviceRegisterRequest, DeviceRegisterResponse, ProofOfWork
from app.finance.livelihood import DEFAULT_DAILY_WAGE_VND
from app.security.blind_index import BlindIndexer, BlindIndexKind
from app.security.device_auth import load_device_public_key
from app.security.keys import SecurityConfigError
from app.security.phone import PhoneNormalizationError, normalize_phone_e164
from app.security.signing import Ed25519Signer, signed_epoch_offset
from app.services.audit_pipeline.policy import POLICY_VERSION
from app.services.audit_pipeline.repository import DeviceRecord, DeviceRepository

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger("vayshield.audit_pipeline.register")

POW_WINDOW_S: Final = 300
PHONE_OPTIN_TTL: Final = timedelta(days=90)  # EC-DB03
REGISTER_PER_HOUR_NO_POW: Final = 10
REGISTER_PER_HOUR_WITH_POW: Final = 120  # CGNAT: nhiều máy chung 1 IP
_REG_KEY: Final = "vs:reg:ip:"


def leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def verify_proof_of_work(
    spki_b64: str, pow_: ProofOfWork, difficulty_bits: int, now_s: int
) -> None:
    if abs(now_s - pow_.timestamp) > POW_WINDOW_S:
        raise AppError(
            ErrorCode.MALFORMED_REQUEST,
            400,
            details=[ErrorDetail(field="body.proof_of_work.timestamp", issue="out_of_window")],
        )
    digest = hashlib.sha256(f"{spki_b64}:{pow_.timestamp}:{pow_.nonce}".encode()).digest()
    if leading_zero_bits(digest) < difficulty_bits:
        raise AppError(
            ErrorCode.MALFORMED_REQUEST,
            400,
            details=[ErrorDetail(field="body.proof_of_work.nonce", issue="insufficient_work")],
        )


def decode_spki(spki_b64: str) -> bytes:
    try:
        raw = base64.b64decode(spki_b64 + "=" * (-len(spki_b64) % 4), validate=True)
    except (binascii.Error, ValueError):
        raise _bad_key() from None
    try:
        load_device_public_key(raw)
    except AppError:
        raise _bad_key() from None
    if len(raw) == 65:  # điểm không nén cũng hợp lệ nhưng contract yêu cầu SPKI DER
        raise _bad_key()
    return raw


def _bad_key() -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        422,
        details=[ErrorDetail(field="body.public_key_spki_b64", issue="invalid_p256_spki")],
    )


async def claim_proof_of_work(redis: Redis | None, spki_b64: str, pow_: ProofOfWork) -> None:
    """PoW dùng MỘT LẦN: SET NX theo hash (TTL 2× cửa sổ) — không gửi lại PoW cũ để tạo
    nhiều device_id (Sybil)."""
    if redis is None:
        return
    key = (
        "vs:pow:" + hashlib.sha256(f"{spki_b64}:{pow_.timestamp}:{pow_.nonce}".encode()).hexdigest()
    )
    try:
        first = await redis.set(key, "1", nx=True, ex=2 * POW_WINDOW_S)
    except Exception:
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503) from None
    if not first:
        raise AppError(
            ErrorCode.MALFORMED_REQUEST,
            400,
            details=[ErrorDetail(field="body.proof_of_work.nonce", issue="already_used")],
        )


async def throttle_registration(
    redis: Redis | None, ip: str, *, has_pow: bool, strict: bool
) -> None:
    """Fixed-window theo giờ trên IP đã băm. Thiếu/hỏng Redis: ``strict`` → 503 (fail-closed)."""
    if redis is None:
        if strict:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
        return
    limit = REGISTER_PER_HOUR_WITH_POW if has_pow else REGISTER_PER_HOUR_NO_POW
    key = f"{_REG_KEY}{'p' if has_pow else 'n'}:{hashlib.sha256(ip.encode()).hexdigest()[:24]}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, 3600)
        ttl = int(await redis.ttl(key))
    except Exception:
        logger.warning("register_throttle_redis_failed")
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503) from None
    if count > limit:
        raise AppError(
            ErrorCode.RATE_LIMITED,
            429,
            headers={"Retry-After": str(max(1, ttl if ttl > 0 else 3600))},
        )


async def register_device(
    body: DeviceRegisterRequest,
    *,
    settings: Settings,
    clock: Clock,
    repo: DeviceRepository,
    signer: Ed25519Signer | None,
    indexer: BlindIndexer | None,
    redis: Redis | None,
    ip: str,
    strict: bool,
) -> DeviceRegisterResponse:
    now = clock.now()
    has_pow = body.proof_of_work is not None
    if body.proof_of_work is not None:
        verify_proof_of_work(
            body.public_key_spki_b64,
            body.proof_of_work,
            settings.pow_difficulty_bits,
            int(now.timestamp()),
        )
    await throttle_registration(redis, ip, has_pow=has_pow, strict=strict)
    pubkey = decode_spki(body.public_key_spki_b64)
    if signer is None:
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
    if await repo.pubkey_exists(pubkey):  # 1 khóa = 1 thiết bị
        raise AppError(
            ErrorCode.CONFLICT,
            409,
            details=[ErrorDetail(field="body.public_key_spki_b64", issue="already_registered")],
        )

    if body.proof_of_work is not None:  # chỉ tiêu PoW khi chắc chắn đăng ký được (không 409/503)
        await claim_proof_of_work(redis, body.public_key_spki_b64, body.proof_of_work)

    phone_hmac: str | None = None
    pepper_version = 1
    if body.phone_e164_optin is not None:
        try:
            e164 = normalize_phone_e164(body.phone_e164_optin)
        except PhoneNormalizationError:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                422,
                details=[ErrorDetail(field="body.phone_e164_optin", issue="invalid_phone")],
            ) from None
        if indexer is not None:
            try:
                lookup = indexer.lookup(BlindIndexKind.PHONE, e164)
                phone_hmac, pepper_version = lookup.current, lookup.current_version
            except SecurityConfigError:
                logger.warning("register_phone_pepper_missing")

    wage = body.daily_wage_vnd or DEFAULT_DAILY_WAGE_VND
    device_id = new_uuid7()
    record = DeviceRecord(
        device_id=device_id,
        pubkey_spki=pubkey,
        platform_type=body.platform_type.value,
        daily_wage_vnd=wage,
        policy_version=POLICY_VERSION,
        phone_blind_hmac=phone_hmac,
        pepper_version=pepper_version,
        phone_optin_exp_at=(now + PHONE_OPTIN_TTL) if phone_hmac is not None else None,
    )
    await repo.create(record)
    return DeviceRegisterResponse(
        device_id=device_id,
        policy_version=POLICY_VERSION,
        daily_wage_vnd=wage,
        # Cùng dạng phản hồi dù SĐT có liên kết được hay không (không làm oracle dò SĐT).
        phone_optin_expires_at=record.phone_optin_exp_at,
        server_epoch=signed_epoch_offset(clock, signer, device_id=device_id),
    )
