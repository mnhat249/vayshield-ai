"""Ghép đôi người thân đồng thuận 2 chiều + phiên người thân (File 4 §3.1, S2-EC23/EC24).

- Người vay tạo mã 6 ký tự (bảng chữ bỏ I/O/0/1, ``secrets``), TTL 10 phút, lưu
  ``vs:pair:code:{code}`` HASH ``{link_id, device_id, fail_attempts}``; ``pairing_code_hash`` trong
  PostgreSQL là SHA-256 của mã.
- Người thân nhập mã: mã bị lấy ra NGUYÊN TỬ (Lua) nên dùng đúng 1 lần dù 2 người cùng nhập.
- Thử sai: mã không tồn tại thì không có phiên để đếm, nên bộ đếm sai đặt theo IP (băm SHA-256, TTL
  = TTL mã). Sai ``pairing_max_fail_attempts`` (3) lần → 423 ``PAIRING_LOCKED`` cho tới hết TTL,
  kể cả khi sau đó nhập đúng mã. Ngoài ra pair-confirm còn qua rate limit IP riêng.
- Phiên người thân = token mờ ``gst_...`` (Redis ``vs:guardian:session:{sha256}``); chỉ lưu băm.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.errors import AppError, ErrorCode, ErrorDetail
from app.core.ids import new_uuid7
from app.core.schemas.family import (
    PairConfirmRequest,
    PairConfirmResponse,
    PairInitRequest,
    PairInitResponse,
)
from app.services.realtime.models import GuardianLink
from app.services.realtime.repository import LIVE_STATUSES, FamilyRepository
from app.services.realtime.senders import is_allowed_push_endpoint

PAIRING_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # khớp PAIRING_CODE_PATTERN
CODE_LEN: Final = 6
MAX_CODE_COLLISION_RETRIES: Final = 8

# KEYS[1]=code key. ARGV: link_id, device_id, ttl_s. 1 = tạo, 0 = đã tồn tại (va chạm mã).
_CREATE_CODE_LUA: Final = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('HSET', KEYS[1], 'link_id', ARGV[1], 'device_id', ARGV[2], 'fail_attempts', '0')
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return 1
"""
# Lấy rồi xóa nguyên tử: trả {link_id, device_id} hoặc rỗng.
_CONSUME_CODE_LUA: Final = """
local v = redis.call('HMGET', KEYS[1], 'link_id', 'device_id')
if not v[1] then return {} end
redis.call('DEL', KEYS[1])
return v
"""


def pair_code_key(code: str) -> str:
    return f"vs:pair:code:{code}"


def pair_fail_key(ip: str) -> str:
    return "vs:pair:fail:" + hashlib.sha256(ip.strip().encode()).hexdigest()[:32]


def guardian_session_key(token: str) -> str:
    return "vs:guardian:session:" + hashlib.sha256(token.encode()).hexdigest()


def generate_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(CODE_LEN))


@dataclass(frozen=True, slots=True)
class GuardianSession:
    link: GuardianLink


class PairingService:
    def __init__(
        self,
        *,
        redis: Redis,
        clock: Clock,
        repo: FamilyRepository,
        code_ttl_s: int = 600,
        max_fail_attempts: int = 3,
        session_ttl_s: int = 180 * 24 * 3600,
    ) -> None:
        self._redis = redis
        self._clock = clock
        self._repo = repo
        self.code_ttl_s = code_ttl_s
        self.max_fail_attempts = max_fail_attempts
        self.session_ttl_s = session_ttl_s
        self._create_script = redis.register_script(_CREATE_CODE_LUA)
        self._consume_script = redis.register_script(_CONSUME_CODE_LUA)

    # --- người vay tạo mã --------------------------------------------------------------------
    async def pair_init(self, device_id: UUID, body: PairInitRequest) -> PairInitResponse:
        link_id = new_uuid7()
        now = self._clock.now()
        expires_at = now + timedelta(seconds=self.code_ttl_s)
        code = ""
        for _ in range(MAX_CODE_COLLISION_RETRIES):
            candidate = generate_code()
            created = await self._create_script(
                keys=[pair_code_key(candidate)],
                args=[str(link_id), str(device_id), self.code_ttl_s],
            )
            if int(created) == 1:
                code = candidate
                break
        if not code:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
        await self._repo.create_pending(
            link_id=link_id,
            device_id=device_id,
            guardian_alias=body.guardian_alias,
            borrower_display_label=body.borrower_display_label,
            pairing_code_hash=hashlib.sha256(code.encode()).hexdigest(),
            pairing_expires_at=expires_at,
        )
        return PairInitResponse(
            link_id=link_id,
            pairing_code=code,
            qr_payload=f"vayshield://pair?code={code}",
            expires_at=expires_at,
            max_fail_attempts=self.max_fail_attempts,
        )

    # --- người thân xác nhận -----------------------------------------------------------------
    async def pair_confirm(self, client_ip: str, body: PairConfirmRequest) -> PairConfirmResponse:
        if not body.consent_accepted:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                422,
                details=[ErrorDetail(field="body.consent_accepted", issue="consent_required")],
            )
        sub = body.webpush_subscription
        if sub is not None and not is_allowed_push_endpoint(sub.endpoint):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                422,
                details=[
                    ErrorDetail(field="body.webpush_subscription.endpoint", issue="not_allowed")
                ],
            )
        fail_key = pair_fail_key(client_ip)
        failures = int(await self._redis.get(fail_key) or 0)
        if failures >= self.max_fail_attempts:
            raise AppError(ErrorCode.PAIRING_LOCKED, 423)

        res = await self._consume_script(keys=[pair_code_key(body.pairing_code)], args=[])
        if not res:
            count = int(await self._redis.incr(fail_key))
            if count == 1:
                await self._redis.expire(fail_key, self.code_ttl_s)
            if count >= self.max_fail_attempts:
                raise AppError(ErrorCode.PAIRING_LOCKED, 423)
            raise AppError(ErrorCode.PAIRING_CODE_INVALID, 404)
        link_id = UUID(_s(res[0]))
        link = await self._repo.activate(
            link_id,
            push_subscription=sub,
            fallback_contact=body.fallback_contact,
            consented_at=self._clock.now(),
        )
        if link is None:  # link đã bị hủy trong lúc chờ
            raise AppError(ErrorCode.PAIRING_CODE_INVALID, 404)
        await self._redis.delete(fail_key)
        token = "gst_" + secrets.token_urlsafe(32)
        await self._redis.set(guardian_session_key(token), str(link.link_id), ex=self.session_ttl_s)
        return PairConfirmResponse(
            link_id=link.link_id,
            status="ACTIVE",
            guardian_alias=link.guardian_alias,
            borrower_display_label=link.borrower_display_label,
            guardian_session_token=token,
            guardian_session_expires_at=self._clock.now() + timedelta(seconds=self.session_ttl_s),
        )

    # --- phiên người thân ----------------------------------------------------------------------
    async def resolve_session(self, token: str | None) -> GuardianSession:
        """Token → link còn hiệu lực; sai/hết hạn/link bị hủy → 401 (không phân biệt lý do)."""
        invalid = AppError(ErrorCode.UNAUTHORIZED, 401)
        if not token or not token.startswith("gst_") or len(token) > 128:
            raise invalid
        raw = await self._redis.get(guardian_session_key(token))
        if not raw:
            raise invalid
        try:
            link_id = UUID(_s(raw))
        except ValueError:
            raise invalid from None
        link = await self._repo.get_link(link_id)
        if link is None or link.status not in LIVE_STATUSES:
            raise invalid
        return GuardianSession(link=link)


def _s(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)
