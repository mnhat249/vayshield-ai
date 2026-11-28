"""Phiên Sandbox ephemeral (EC-DB16): mỗi lần tạo = 1 ``sandbox_namespace`` + 1 ``device_id`` mới.

- Token ``sbx_...`` sinh bằng ``secrets``; Redis chỉ lưu SHA-256 của token (lộ Redis ≠ lộ token).
- ``vip_code`` (mã tờ A4) chỉ cấp QUOTA (tier ``JUDGE_VIP``), KHÔNG quyết định namespace: hai giám
  khảo dùng chung mã vẫn có hai namespace độc lập. Mã sai → tier ``PUBLIC`` (không lộ mã đúng/sai;
  UI thấy ``tier`` trong phản hồi). So sánh hằng thời gian.
- EC-OPS10: tối đa ``SANDBOX_MAX_SESSIONS_PER_IP_PER_HOUR`` phiên PUBLIC/IP/giờ. Phiên VIP không
  bị trần IP (giám khảo ở văn phòng/NAT chung, EC-OPS05) — bot không biết mã.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_uuid7
from app.core.schemas.sandbox import SandboxTier
from app.sandbox.budget import BudgetBreaker
from app.services.realtime.models import BorrowerPrincipal

TOKEN_PREFIX: Final = "sbx_"  # noqa: S105 - tiền tố định dạng token, không phải secret
MAX_TOKEN_LEN: Final = 128
REQUEST_COST_MICRO: Final = 2_000  # 0.002 USD ước tính cho mỗi request AI-capable của phiên
EVENT_COST_MICRO: Final = 1_000  # 0.001 USD cho mỗi sự kiện giả lập
LIVE_NAMESPACE: Final = "live"


def is_sandbox_namespace(namespace: str) -> bool:
    return namespace != LIVE_NAMESPACE


def session_key(token_hash: str) -> str:
    return f"vs:sbx:sess:{token_hash}"


def context_key(namespace: str) -> str:
    return f"vs:sbx:ctx:{namespace}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SandboxSession:
    token_hash: str
    namespace: str
    device_id: UUID
    tier: SandboxTier
    expires_at: datetime

    def principal(self) -> BorrowerPrincipal:
        return BorrowerPrincipal(device_id=self.device_id, sandbox_namespace=self.namespace)


@dataclass(frozen=True, slots=True)
class CreatedSession:
    token: str
    session: SandboxSession
    remaining_micro: int


class SandboxSessionManager:
    def __init__(
        self, redis: Redis, clock: Clock, settings: Settings, budget: BudgetBreaker
    ) -> None:
        self._redis = redis
        self._clock = clock
        self._settings = settings
        self._budget = budget

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def budget(self) -> BudgetBreaker:
        return self._budget

    def _tier_for(self, vip_code: str | None) -> SandboxTier:
        expected = self._settings.sandbox_vip_code
        if vip_code and expected is not None:
            secret = expected.get_secret_value()
            if secret and hmac.compare_digest(vip_code.encode(), secret.encode()):
                return SandboxTier.JUDGE_VIP
        return SandboxTier.PUBLIC

    async def _enforce_ip_cap(self, ip: str) -> None:
        hour = int(self._clock.now().timestamp()) // 3600
        key = f"vs:sbx:ip:{hashlib.sha256(ip.encode()).hexdigest()[:24]}:{hour}"
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, 3600 + 60)
        if count > self._settings.sandbox_max_sessions_per_ip_per_hour:
            raise AppError(ErrorCode.RATE_LIMITED, 429, headers={"Retry-After": "3600"})

    async def create(self, vip_code: str | None, ip: str) -> CreatedSession:
        if not self._settings.sandbox_enabled:
            raise AppError(ErrorCode.NOT_FOUND, 404)
        tier = self._tier_for(vip_code)
        try:
            if tier is SandboxTier.PUBLIC:
                await self._enforce_ip_cap(ip)
            await self._budget.assert_available(tier)
            token = TOKEN_PREFIX + secrets.token_urlsafe(24)
            namespace = self._settings.sandbox_namespace_prefix + secrets.token_hex(6)
            device_id = new_uuid7()
            ttl = self._settings.sandbox_session_ttl_s
            expires_ms = int(self._clock.now().timestamp() * 1000) + ttl * 1000
            key = session_key(hash_token(token))
            await self._redis.hset(
                key,
                mapping={
                    "namespace": namespace,
                    "device_id": str(device_id),
                    "tier": tier.value,
                    "expires_ms": str(expires_ms),
                },
            )
            await self._redis.expire(key, ttl)
            remaining = await self._budget.remaining_micro(tier)
        except RedisError as exc:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503) from exc
        session = SandboxSession(
            token_hash=hash_token(token),
            namespace=namespace,
            device_id=device_id,
            tier=tier,
            expires_at=datetime.fromtimestamp(expires_ms / 1000, tz=UTC),
        )
        return CreatedSession(token=token, session=session, remaining_micro=remaining)

    async def lookup(self, token: str | None) -> SandboxSession | None:
        """Tra phiên theo token; ``None`` nếu sai/hết hạn/tắt Sandbox (không phân biệt lý do)."""
        if not self._settings.sandbox_enabled or not token:
            return None
        if len(token) > MAX_TOKEN_LEN or not token.startswith(TOKEN_PREFIX):
            return None
        token_hash = hash_token(token)
        try:
            raw = await self._redis.hgetall(session_key(token_hash))
        except RedisError as exc:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503) from exc
        if not raw:
            return None
        try:
            return SandboxSession(
                token_hash=token_hash,
                namespace=str(raw["namespace"]),
                device_id=UUID(str(raw["device_id"])),
                tier=SandboxTier(str(raw["tier"])),
                expires_at=datetime.fromtimestamp(int(raw["expires_ms"]) / 1000, tz=UTC),
            )
        except (KeyError, ValueError):
            return None

    async def authenticate(self, token: str | None, cost_micro: int) -> SandboxSession:
        """Tra phiên + trừ ngân sách (cầu chì). Phiên sai → 401; hết ngân sách → 429."""
        session = await self.lookup(token)
        if session is None:
            raise AppError(ErrorCode.UNAUTHORIZED, 401)
        await self._budget.charge(session.tier, cost_micro)
        return session

    async def resolve(self, token: str) -> BorrowerPrincipal | None:
        """``SandboxPrincipalResolver`` cho T12/T11: phiên hợp lệ → principal namespace riêng,
        mỗi lần gọi trừ ``REQUEST_COST_MICRO`` (các route AI-capable dùng chung cầu chì)."""
        session = await self.lookup(token)
        if session is None:
            return None
        await self._budget.charge(session.tier, REQUEST_COST_MICRO)
        return session.principal()
