"""Rate limit kép phân tầng (Composite Token Bucket) trên Redis — EC-DB25.

500 công nhân KCN dùng chung 1 IP CGNAT không được bị chặn nhầm:

1. Bucket theo THIẾT BỊ (``RATE_LIMIT_DEVICE_RPM``, mặc định 60/phút) xét TRƯỚC.
2. Bucket tổng theo IP (``RATE_LIMIT_IP_RPM``, mặc định 3000/phút ≈ 50× thiết bị) xét SAU.

Cả 2 bucket kiểm và trừ trong 1 script Lua nguyên tử: chỉ trừ token khi cả 2 đều còn, nên request
bị từ chối ở tầng IP không "đốt" token của thiết bị và ngược lại. IP được băm SHA-256 trước khi làm
khóa Redis (không lưu IP thô). ``device_id`` truyền vào phải là thiết bị ĐÃ xác thực chữ ký.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode

DEVICE_KEY_PREFIX: Final = "vs:rl:dev:"
IP_KEY_PREFIX: Final = "vs:rl:ip:"
_WINDOW_MS: Final = 60_000

# KEYS: [dev_key, ip_key] (dev_key = "" nếu không có thiết bị)
# ARGV: now_ms, dev_capacity, ip_capacity, window_ms
# Trả: {allowed(0/1), blocked_scope(0 none/1 device/2 ip), retry_after_ms}
_LUA: Final = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[4])
local function load(key, cap)
  local data = redis.call('HMGET', key, 't', 'ts')
  local tokens = tonumber(data[1])
  local ts = tonumber(data[2])
  if tokens == nil or ts == nil then
    return cap, now
  end
  if now > ts then
    tokens = math.min(cap, tokens + (now - ts) * cap / window)
  end
  return tokens, now
end
local function wait_ms(tokens, cap)
  return math.ceil((1 - tokens) * window / cap)
end
local has_dev = KEYS[1] ~= ''
local dev_cap = tonumber(ARGV[2])
local ip_cap = tonumber(ARGV[3])
local dev_tokens = 0
if has_dev then
  dev_tokens = load(KEYS[1], dev_cap)
  if dev_tokens < 1 then
    return {0, 1, wait_ms(dev_tokens, dev_cap)}
  end
end
local ip_tokens = load(KEYS[2], ip_cap)
if ip_tokens < 1 then
  return {0, 2, wait_ms(ip_tokens, ip_cap)}
end
if has_dev then
  redis.call('HSET', KEYS[1], 't', tostring(dev_tokens - 1), 'ts', tostring(now))
  redis.call('PEXPIRE', KEYS[1], window * 2)
end
redis.call('HSET', KEYS[2], 't', tostring(ip_tokens - 1), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[2], window * 2)
return {1, 0, 0}
"""


class LimitScope(StrEnum):
    NONE = "NONE"
    DEVICE = "DEVICE"
    IP = "IP"


_SCOPES: Final = {0: LimitScope.NONE, 1: LimitScope.DEVICE, 2: LimitScope.IP}


@dataclass(frozen=True, slots=True)
class RateDecision:
    allowed: bool
    blocked_scope: LimitScope
    retry_after_s: int


def ip_bucket_key(ip: str) -> str:
    return IP_KEY_PREFIX + hashlib.sha256(ip.strip().encode("utf-8")).hexdigest()[:32]


def device_bucket_key(device_id: UUID) -> str:
    return f"{DEVICE_KEY_PREFIX}{device_id}"


class CompositeRateLimiter:
    def __init__(self, redis: Redis, *, device_rpm: int, ip_rpm: int) -> None:
        if device_rpm < 1 or ip_rpm < 1:
            raise ValueError("rpm phải >= 1")
        self._redis = redis
        self.device_rpm = device_rpm
        self.ip_rpm = ip_rpm

    @classmethod
    def from_settings(cls, redis: Redis, settings: Settings | None = None) -> CompositeRateLimiter:
        cfg = settings or get_settings()
        return cls(redis, device_rpm=cfg.rate_limit_device_rpm, ip_rpm=cfg.rate_limit_ip_rpm)

    async def check(self, *, device_id: UUID | None, ip: str, clock: Clock) -> RateDecision:
        now_ms = int(clock.now().timestamp() * 1000)
        dev_key = device_bucket_key(device_id) if device_id is not None else ""
        result = await self._redis.eval(
            _LUA,
            2,
            dev_key,
            ip_bucket_key(ip),
            now_ms,
            self.device_rpm,
            self.ip_rpm,
            _WINDOW_MS,
        )
        allowed, scope, retry_ms = (int(x) for x in result)
        return RateDecision(
            allowed=bool(allowed),
            blocked_scope=_SCOPES[scope],
            retry_after_s=max(1, math.ceil(retry_ms / 1000)) if not allowed else 0,
        )

    async def enforce(self, *, device_id: UUID | None, ip: str, clock: Clock) -> None:
        """Như ``check`` nhưng vượt ngưỡng → ``AppError`` 429 kèm ``Retry-After``."""
        decision = await self.check(device_id=device_id, ip=ip, clock=clock)
        if not decision.allowed:
            raise AppError(
                ErrorCode.RATE_LIMITED,
                429,
                headers={"Retry-After": str(decision.retry_after_s)},
            )


def client_ip(scope_client: tuple[str, int] | None) -> str:
    """IP từ kết nối TCP (``request.client``). KHÔNG tin ``X-Forwarded-For`` khi chưa cấu hình
    proxy tin cậy (giả định an toàn — xem báo cáo T10)."""
    if scope_client is None:
        return "unknown"
    return scope_client[0]
