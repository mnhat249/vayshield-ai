"""Lưu/đọc trạng thái HSM trên Redis HASH ``vs:shield:state:{device_id}`` (File 4 §3.1, §3.2).

- Ghi bằng CAS theo ``version`` (Lua) + vòng thử lại: đọc → dựng ``FiberSupervisor`` → áp thao tác
  → ghi nếu phiên bản chưa đổi. Không mất cập nhật khi nhiều nguồn ghi đồng thời (EC-DB07,
  S2-EC43).
- Đọc có timeout (mặc định 80ms, EC-DB11). Redis chậm/mất → đọc xuyên ``fallback`` (PostgreSQL
  ``protection_sessions``, do tầng repository cung cấp); không có/lỗi → trạng thái an toàn
  (``S4_SHIELD_ARMED_24H``, ``degraded=True``) — thà hỏi lại 1 chạm còn hơn bỏ lọt.
- Cache miss (Redis khởi động lại, mất dữ liệu) → read-through fallback + tái nạp cache.

Thời gian luôn do caller truyền (``now_ms`` epoch ms từ ``Clock.now()``), không đọc đồng hồ ở đây.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import AppError, ErrorCode
from app.core.schemas.common import HsmState
from app.shield_state.fibers import (
    EvidenceSource,
    FiberSupervisor,
    LoanJourneyFiber,
    RiskEvaluation,
)
from app.shield_state.hsm import HsmEvent
from app.shield_state.keys import (
    load_lua,
    shield_state_key,
    shield_state_ttl_ms,
    smurf_window_key,
)
from app.shield_state.risk import (
    RiskTier,
    SensorContext,
    call_synergy,
    disbursement_active,
    in_golden_window,
    projection_term,
    risk_tier,
)

T = TypeVar("T")

FallbackReader = Callable[[str], Awaitable[Sequence[LoanJourneyFiber] | None]]

DEFAULT_REDIS_TIMEOUT_MS: Final = 80
DEFAULT_FALLBACK_TIMEOUT_MS: Final = 500
MAX_CAS_RETRIES: Final = 16
SAFE_DEFAULT_STATE: Final = HsmState.S4_SHIELD_ARMED_24H


class SnapshotSource(StrEnum):
    REDIS = "REDIS"
    PG_FALLBACK = "PG_FALLBACK"
    SAFE_DEFAULT = "SAFE_DEFAULT"
    EMPTY = "EMPTY"


@dataclass(frozen=True, slots=True)
class DeviceShieldSnapshot:
    device_id: str
    fibers: tuple[LoanJourneyFiber, ...]
    version: int
    state: HsmState
    shield_expires_at_ms: int | None
    source: SnapshotSource
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class BankOpenEvaluation:
    probability: float
    log_odds: float
    tier: RiskTier
    open_count_15m: int
    golden_window: bool
    degraded: bool = False


class _RedisDownError(Exception):
    pass


def _encode(sup: FiberSupervisor, now_ms: int) -> dict[str, str]:
    fibers = [f.to_dict() for f in sup.fibers]
    base = sup.base_log_odds(now_ms)
    expires = sup.shield_expires_at_ms()
    disb = sup.latest_disbursement_ms()
    return {
        "fibers": json.dumps(fibers, separators=(",", ":"), sort_keys=True),
        "state": sup.device_state().value,
        "log_odds": repr(base),
        "base_log_odds": repr(base),
        "disb_ts": "" if disb is None else str(disb),
        "shield_expires_at": "" if expires is None else str(expires),
        "updated_at_ms": str(now_ms),
    }


def _decode_fibers(raw: dict[str, str]) -> tuple[LoanJourneyFiber, ...] | None:
    """None = cache miss hoặc dữ liệu hỏng (xử lý như miss → đọc xuyên)."""
    payload = raw.get("fibers")
    if payload is None:
        return None
    try:
        data: Any = json.loads(payload)
        return tuple(LoanJourneyFiber.from_dict(d) for d in data)
    except (ValueError, KeyError, TypeError):
        return None


class ShieldStateStore:
    def __init__(
        self,
        redis: Redis,
        *,
        fallback: FallbackReader | None = None,
        redis_timeout_ms: int = DEFAULT_REDIS_TIMEOUT_MS,
        fallback_timeout_ms: int = DEFAULT_FALLBACK_TIMEOUT_MS,
        max_cas_retries: int = MAX_CAS_RETRIES,
    ) -> None:
        self._redis = redis
        self._fallback = fallback
        self._timeout_s = redis_timeout_ms / 1000
        self._fallback_timeout_s = fallback_timeout_ms / 1000
        self._max_retries = max_cas_retries
        self._cas = redis.register_script(load_lua("cas_write_state.lua"))
        self._smurf = redis.register_script(load_lua("atomic_smurf_and_risk_eval.lua"))

    # --- I/O thấp tầng ----------------------------------------------------------------------
    async def _read_raw(self, device_id: str) -> dict[str, str]:
        try:
            async with asyncio.timeout(self._timeout_s):
                raw = await self._redis.hgetall(shield_state_key(device_id))
                # Client tạo với decode_responses=True → khóa/giá trị là str.
                return {str(k): str(v) for k, v in raw.items()}
        except (TimeoutError, RedisError, OSError) as exc:
            raise _RedisDownError from exc

    async def _cas_write(
        self, device_id: str, expected_version: int, sup: FiberSupervisor, now_ms: int
    ) -> int:
        fields = _encode(sup, now_ms)
        args: list[str | int] = [
            expected_version,
            shield_state_ttl_ms(now_ms, sup.shield_expires_at_ms()),
        ]
        for k, v in fields.items():
            args.extend([k, v])
        try:
            async with asyncio.timeout(self._timeout_s):
                res = await self._cas(keys=[shield_state_key(device_id)], args=args)
        except (TimeoutError, RedisError, OSError) as exc:
            raise _RedisDownError from exc
        return int(res)

    async def _read_fallback(self, device_id: str) -> tuple[LoanJourneyFiber, ...] | None:
        if self._fallback is None:
            return None
        try:
            async with asyncio.timeout(self._fallback_timeout_s):
                fibers = await self._fallback(device_id)
        except Exception:  # fallback lỗi không được làm sập luồng bảo vệ
            return None
        return None if fibers is None else tuple(fibers)

    @staticmethod
    def _snapshot(
        device_id: str,
        fibers: Sequence[LoanJourneyFiber],
        version: int,
        source: SnapshotSource,
        *,
        degraded: bool = False,
    ) -> DeviceShieldSnapshot:
        sup = FiberSupervisor(fibers)
        return DeviceShieldSnapshot(
            device_id=device_id,
            fibers=sup.fibers,
            version=version,
            state=sup.device_state(),
            shield_expires_at_ms=sup.shield_expires_at_ms(),
            source=source,
            degraded=degraded,
        )

    # --- Đọc --------------------------------------------------------------------------------
    async def load(self, device_id: str, now_ms: int) -> DeviceShieldSnapshot:
        try:
            raw = await self._read_raw(device_id)
        except _RedisDownError:
            fb = await self._read_fallback(device_id)
            if fb is not None:
                return self._snapshot(device_id, fb, 0, SnapshotSource.PG_FALLBACK, degraded=True)
            return DeviceShieldSnapshot(
                device_id=device_id,
                fibers=(),
                version=0,
                state=SAFE_DEFAULT_STATE,
                shield_expires_at_ms=None,
                source=SnapshotSource.SAFE_DEFAULT,
                degraded=True,
            )
        version = int(raw.get("version") or 0)
        fibers = _decode_fibers(raw)
        if fibers is not None:
            return self._snapshot(device_id, fibers, version, SnapshotSource.REDIS)
        fb = await self._read_fallback(device_id)
        if fb is None:
            return self._snapshot(device_id, (), version, SnapshotSource.EMPTY)
        # Read-through: tái nạp cache (best-effort; xung đột/lỗi thì lần sau nạp lại).
        sup = FiberSupervisor(fb)
        try:
            new_version = await self._cas_write(device_id, version, sup, now_ms)
        except _RedisDownError:
            new_version = -1
        return self._snapshot(device_id, fb, max(new_version, version), SnapshotSource.PG_FALLBACK)

    # --- Ghi (CAS) --------------------------------------------------------------------------
    async def mutate(
        self, device_id: str, now_ms: int, op: Callable[[FiberSupervisor], T]
    ) -> tuple[DeviceShieldSnapshot, T]:
        """Áp ``op`` nguyên tử theo kiểu optimistic. ``op`` phải thuần (có thể chạy lại)."""
        for _ in range(self._max_retries):
            try:
                raw = await self._read_raw(device_id)
            except _RedisDownError as exc:
                raise AppError(
                    ErrorCode.SERVICE_DEGRADED,
                    503,
                    "Bộ nhớ trạng thái Khiên tạm thời gián đoạn, vui lòng thử lại.",
                ) from exc
            version = int(raw.get("version") or 0)
            fibers = _decode_fibers(raw)
            if fibers is None:
                fibers = await self._read_fallback(device_id) or ()
            sup = FiberSupervisor(fibers)
            sup.expire(now_ms)
            result = op(sup)
            try:
                new_version = await self._cas_write(device_id, version, sup, now_ms)
            except _RedisDownError as exc:
                raise AppError(
                    ErrorCode.SERVICE_DEGRADED,
                    503,
                    "Bộ nhớ trạng thái Khiên tạm thời gián đoạn, vui lòng thử lại.",
                ) from exc
            if new_version >= 0:
                return (
                    self._snapshot(device_id, sup.fibers, new_version, SnapshotSource.REDIS),
                    result,
                )
        raise AppError(ErrorCode.CONFLICT, 409, "Trạng thái Khiên đang được cập nhật đồng thời.")

    async def apply_event(
        self,
        device_id: str,
        fiber_key: str,
        event: HsmEvent,
        *,
        now_ms: int,
        event_id: str,
        source: EvidenceSource | None = None,
    ) -> DeviceShieldSnapshot:
        snap, _ = await self.mutate(
            device_id,
            now_ms,
            lambda s: s.apply_event(
                fiber_key, event, now_ms=now_ms, event_id=event_id, source=source
            ),
        )
        return snap

    async def arm_shield(
        self,
        device_id: str,
        fiber_key: str,
        event: HsmEvent,
        *,
        now_ms: int,
        event_id: str,
        source: EvidenceSource,
        shield_ms: int | None = None,
    ) -> tuple[DeviceShieldSnapshot, LoanJourneyFiber]:
        return await self.mutate(
            device_id,
            now_ms,
            lambda s: s.arm_disbursement(
                fiber_key,
                event,
                now_ms=now_ms,
                event_id=event_id,
                source=source,
                shield_ms=shield_ms,
            ),
        )

    # --- Đánh giá mở app ngân hàng (Lua nguyên tử) -------------------------------------------
    async def evaluate_bank_open(
        self,
        device_id: str,
        session_id: str,
        event_id: str,
        *,
        now_ms: int,
        sensors: SensorContext,
    ) -> BankOpenEvaluation:
        """Ψ/Ξ/Ω + cửa sổ smurf 15 phút tính trong Lua; phần nền tính từ các luồng tại ``now_ms``.
        ``sensors.bank_open_count_15m`` bị bỏ qua (Redis là nguồn đếm)."""
        snap = await self.load(device_id, now_ms)
        sup = FiberSupervisor(snap.fibers)
        sup.expire(now_ms)
        evidence = [e for f in sup.active_fibers() for e in f.evidence]
        golden = in_golden_window(evidence, now_ms)
        if snap.degraded:
            return self._degraded_eval(sup, now_ms, sensors, golden)
        synergy = call_synergy(
            disbursement_is_active=disbursement_active(evidence, now_ms),
            audio_mode=sensors.audio_mode,
            ms_since_call_end=sensors.ms_since_call_end,
        )
        proj = projection_term(
            media_projection_active=sensors.media_projection_active,
            remote_control_active=sensors.remote_control_active,
        )
        base = sup.base_log_odds(now_ms)
        try:
            async with asyncio.timeout(self._timeout_s):
                res = await self._smurf(
                    keys=[smurf_window_key(session_id), shield_state_key(device_id)],
                    args=[
                        now_ms,
                        event_id,
                        repr(synergy),
                        repr(proj),
                        repr(base),
                        shield_state_ttl_ms(now_ms, sup.shield_expires_at_ms()),
                    ],
                )
        except (TimeoutError, RedisError, OSError):
            return self._degraded_eval(sup, now_ms, sensors, golden)
        prob = float(res[0])
        return BankOpenEvaluation(
            probability=prob,
            log_odds=float(res[2]),
            tier=risk_tier(prob, golden_hour_floor=golden),
            open_count_15m=int(res[1]),
            golden_window=golden,
        )

    @staticmethod
    def _degraded_eval(
        sup: FiberSupervisor, now_ms: int, sensors: SensorContext, golden: bool
    ) -> BankOpenEvaluation:
        """Redis mất: tính thuần tại chỗ (đếm smurf tối thiểu 1) và sàn Mức Cam (an toàn)."""
        count = max(1, sensors.bank_open_count_15m)
        ev: RiskEvaluation = sup.evaluate(
            now_ms,
            SensorContext(
                audio_mode=sensors.audio_mode,
                ms_since_call_end=sensors.ms_since_call_end,
                media_projection_active=sensors.media_projection_active,
                remote_control_active=sensors.remote_control_active,
                bank_open_count_15m=count,
            ),
        )
        return BankOpenEvaluation(
            probability=ev.probability,
            log_odds=ev.log_odds,
            tier=risk_tier(ev.probability, golden_hour_floor=True),
            open_count_15m=count,
            golden_window=golden,
            degraded=True,
        )
