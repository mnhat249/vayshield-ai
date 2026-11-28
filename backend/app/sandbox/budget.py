"""Cầu chì ngân sách ngày (File 6 §1.2 mục 3, EC-OPS10).

Mỗi tier có hạn mức USD/ngày riêng (PUBLIC: ``SANDBOX_DAILY_BUDGET_USD``; JUDGE_VIP:
``GEMINI_VIP_DAILY_BUDGET_USD``). Đếm bằng số nguyên micro-USD trong Redis (INCRBY nguyên tử,
không float). Vượt trần → hoàn lại phần vừa cộng và trả 429 ``BUDGET_EXCEEDED`` (fail-closed).
Ngày tính theo giờ Việt Nam (UTC+7) để khớp "ngân sách ngày" của đội vận hành.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.schemas.sandbox import SandboxTier

MICRO: Final = 1_000_000
_BUDGET_KEY_TTL_S: Final = 2 * 24 * 3600
_VN_OFFSET: Final = timedelta(hours=7)


def usd_to_micro(usd: float) -> int:
    return int((Decimal(str(usd)) * MICRO).to_integral_value())


def micro_to_usd(micro: int) -> float:
    return float(Decimal(micro) / MICRO)


def _exceeded() -> AppError:
    return AppError(ErrorCode.BUDGET_EXCEEDED, 429, headers={"Retry-After": "3600"})


def _degraded() -> AppError:
    return AppError(ErrorCode.SERVICE_DEGRADED, 503)


class BudgetBreaker:
    def __init__(self, redis: Redis, settings: Settings, clock: Clock) -> None:
        self._redis = redis
        self._settings = settings
        self._clock = clock

    def limit_micro(self, tier: SandboxTier) -> int:
        usd = (
            self._settings.gemini_vip_daily_budget_usd
            if tier is SandboxTier.JUDGE_VIP
            else self._settings.sandbox_daily_budget_usd
        )
        return usd_to_micro(usd)

    def _key(self, tier: SandboxTier) -> str:
        day = (self._clock.now() + _VN_OFFSET).strftime("%Y%m%d")
        return f"vs:sbx:budget:{tier.value}:{day}"

    async def remaining_micro(self, tier: SandboxTier) -> int:
        try:
            used = int(await self._redis.get(self._key(tier)) or 0)
        except RedisError as exc:
            raise _degraded() from exc
        return max(0, self.limit_micro(tier) - used)

    async def assert_available(self, tier: SandboxTier) -> None:
        if await self.remaining_micro(tier) <= 0:
            raise _exceeded()

    async def charge(self, tier: SandboxTier, micro: int) -> int:
        """Trừ ``micro`` khỏi ngân sách ngày; trả phần còn lại (micro-USD). Vượt → 429."""
        if micro <= 0:
            return await self.remaining_micro(tier)
        key = self._key(tier)
        limit = self.limit_micro(tier)
        try:
            used = int(await self._redis.incrby(key, micro))
            await self._redis.expire(key, _BUDGET_KEY_TTL_S)
            if used > limit:
                await self._redis.decrby(key, micro)
                raise _exceeded()
        except RedisError as exc:
            raise _degraded() from exc
        return limit - used
