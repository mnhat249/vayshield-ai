"""``POST /sandbox/simulate-event``: bắn sự kiện ngoại vi giả lập chạy XUYÊN LÕI THẬT.

- Giải ngân (thông báo ngân hàng): ``ShieldService.arm`` của T12 với ``trigger_origin=SANDBOX_SIM``.
- B2B webhook / mở app ngân hàng / báo đã chuyển tiền: ``ShieldStateStore`` + HSM + Lua của T05
  với nguồn bằng chứng ``SANDBOX_SIM`` (không đi qua đối tác/HMAC thật, không chạm DB đối tác).
- Cuộc gọi: lưu ngữ cảnh cảm biến theo namespace (Redis, TTL phiên) rồi đưa vào Ψ/Ξ khi đánh giá.

Cô lập (EC-DB16): mọi khóa Redis trạng thái Khiên gắn với ``device_id`` riêng của phiên; ngữ cảnh
cuộc gọi gắn với ``sandbox_namespace``. Phiên sandbox KHÔNG báo mule lên đồ thị live (không có
đường code nào ở đây gọi ``mule_graph``), origin luôn ``SANDBOX_SIM``.
"""

from __future__ import annotations

from typing import Final

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.clock import Clock
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_uuid7
from app.core.schemas.common import AudioCallState, HsmState, TriggerOrigin
from app.core.schemas.sandbox import (
    SandboxEventParams,
    SandboxEventType,
    SimulateEventRequest,
    SimulateEventResponse,
)
from app.core.schemas.shield import ShieldArmRequest
from app.sandbox.session import SandboxSession, context_key
from app.services.realtime.shield_service import ShieldService
from app.shield_state.fibers import (
    EvidenceSource,
    FiberSupervisor,
    LoanJourneyFiber,
)
from app.shield_state.hsm import HsmEvent
from app.shield_state.keys import shield_state_key
from app.shield_state.risk import AudioMode, RiskTier, SensorContext
from app.shield_state.store import ShieldStateStore

_ACTIVE_CALLS: Final = frozenset({AudioCallState.ACTIVE_CELLULAR, AudioCallState.ACTIVE_VOIP})


class _CallContext:
    __slots__ = ("call_end_ms", "call_state", "screen_share")

    def __init__(
        self,
        call_state: AudioCallState = AudioCallState.NONE,
        call_end_ms: int | None = None,
        screen_share: bool = False,
    ) -> None:
        self.call_state = call_state
        self.call_end_ms = call_end_ms
        self.screen_share = screen_share

    def sensors(self, now_ms: int) -> SensorContext:
        mode = AudioMode.IN_CALL if self.call_state in _ACTIVE_CALLS else AudioMode.NORMAL
        since: int | None = None
        if self.call_state is AudioCallState.ENDED_WITHIN_180S:
            since = 0 if self.call_end_ms is None else max(0, now_ms - self.call_end_ms)
        return SensorContext(
            audio_mode=mode, ms_since_call_end=since, media_projection_active=self.screen_share
        )


class SandboxSimulator:
    def __init__(self, *, redis: Redis, clock: Clock, shield: ShieldService, ttl_s: int) -> None:
        self._redis = redis
        self._clock = clock
        self._shield = shield
        self._store = ShieldStateStore(redis)
        self._ttl_s = ttl_s

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    # --- ngữ cảnh cuộc gọi (theo namespace) ---------------------------------------------------
    async def _load_ctx(self, namespace: str) -> _CallContext:
        raw = await self._redis.hgetall(context_key(namespace))
        if not raw:
            return _CallContext()
        try:
            end = raw.get("call_end_ms") or ""
            return _CallContext(
                AudioCallState(str(raw.get("call_state", AudioCallState.NONE.value))),
                int(end) if end else None,
                raw.get("screen_share") == "1",
            )
        except ValueError:
            return _CallContext()

    async def _save_ctx(self, namespace: str, ctx: _CallContext) -> None:
        key = context_key(namespace)
        await self._redis.hset(
            key,
            mapping={
                "call_state": ctx.call_state.value,
                "call_end_ms": "" if ctx.call_end_ms is None else str(ctx.call_end_ms),
                "screen_share": "1" if ctx.screen_share else "0",
            },
        )
        await self._redis.expire(key, self._ttl_s)

    # --- điều phối -------------------------------------------------------------------------------
    async def simulate(
        self, session: SandboxSession, body: SimulateEventRequest
    ) -> SimulateEventResponse:
        try:
            return await self._simulate(session, body)
        except RedisError as exc:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503) from exc

    async def _simulate(
        self, session: SandboxSession, body: SimulateEventRequest
    ) -> SimulateEventResponse:
        now_ms = self._now_ms()
        event_id = new_uuid7()
        eid = str(event_id)
        device = str(session.device_id)
        params = body.params
        ctx = await self._load_ctx(session.namespace)
        accepted = True
        side: list[str] = []
        evaluated: tuple[float, float] | None = None  # (log_odds, probability) từ Lua

        match body.event_type:
            case SandboxEventType.BANK_DISBURSEMENT_NOTIF:
                await self._shield.arm(
                    session.principal(),
                    ShieldArmRequest(
                        fiber_key=params.fiber_key,
                        trigger_origin=TriggerOrigin.SANDBOX_SIM,
                        amount_bucket_tier=params.amount_bucket_tier,
                        disbursement_at=self._clock.now(),
                        client_event_id=eid,
                    ),
                )
                side.append("SHIELD_ARMED")
            case SandboxEventType.B2B_DISBURSEMENT_WEBHOOK:
                _, fiber = await self._store.arm_shield(
                    device,
                    params.fiber_key,
                    HsmEvent.E5_B2B_WEBHOOK,
                    now_ms=now_ms,
                    event_id=eid,
                    source=EvidenceSource.SANDBOX_SIM,
                )
                accepted = fiber is not None
                side.append("B2B_SHIELD_ARMED" if accepted else "NO_LOAN_JOURNEY")
            case SandboxEventType.INCOMING_SCAM_CALL:
                ctx = _CallContext(
                    params.call_state or AudioCallState.ACTIVE_VOIP,
                    None,
                    bool(params.screen_share),
                )
                await self._save_ctx(session.namespace, ctx)
                side.append("CALL_CONTEXT_SET")
                if ctx.screen_share:
                    side.append("SCREEN_SHARE_ON")
            case SandboxEventType.CALL_ENDED:
                ctx = _CallContext(AudioCallState.ENDED_WITHIN_180S, now_ms, False)
                await self._save_ctx(session.namespace, ctx)
                side.append("CALL_CONTEXT_SET")
            case SandboxEventType.BANK_APP_OPENED:
                accepted, evaluated = await self._bank_opened(
                    device, params, ctx, now_ms, eid, side
                )
            case SandboxEventType.USER_ALREADY_TRANSFERRED:
                _, out = await self._store.mutate(
                    device,
                    now_ms,
                    lambda s: s.apply_event(
                        params.fiber_key,
                        HsmEvent.E11_USER_ALREADY_TRANSFERRED,
                        now_ms=now_ms,
                        event_id=eid,
                    ),
                )
                accepted = out.accepted
                side.append("GOLDEN_HOUR_TRIAGE" if accepted else "EVENT_REJECTED")
            case SandboxEventType.RESET_SCENARIO:
                await self._redis.delete(shield_state_key(device), context_key(session.namespace))
                ctx = _CallContext()
                side.append("SANDBOX_STATE_RESET")

        snap = await self._store.load(device, now_ms)
        sup = FiberSupervisor(snap.fibers)
        fiber_view: LoanJourneyFiber | None = sup.get(params.fiber_key)
        state: HsmState = fiber_view.state if fiber_view is not None else sup.device_state()
        if evaluated is not None:
            log_odds, prob = evaluated
        else:
            ev = sup.evaluate(now_ms, ctx.sensors(now_ms))
            log_odds, prob = ev.log_odds, ev.probability
        return SimulateEventResponse(
            event_id=event_id,
            sandbox_namespace=session.namespace,
            accepted=accepted,
            resulting_hsm_state=state,
            risk_log_odds=log_odds,
            scam_probability=prob,
            side_effects=side,
        )

    async def _bank_opened(
        self,
        device: str,
        params: SandboxEventParams,
        ctx: _CallContext,
        now_ms: int,
        eid: str,
        side: list[str],
    ) -> tuple[bool, tuple[float, float] | None]:
        snap = await self._store.load(device, now_ms)
        sup = FiberSupervisor(snap.fibers)
        sup.expire(now_ms)
        fiber = sup.get(params.fiber_key) or next(iter(sup.active_fibers()), None)
        if fiber is None:
            side.append("NO_ACTIVE_SHIELD")
            return False, None
        eff = _CallContext(
            params.call_state or ctx.call_state,
            ctx.call_end_ms,
            ctx.screen_share if params.screen_share is None else params.screen_share,
        )
        sensors = eff.sensors(now_ms)
        result = await self._store.evaluate_bank_open(
            device, fiber.session_id, eid, now_ms=now_ms, sensors=sensors
        )
        key = fiber.fiber_key

        def op(s: FiberSupervisor) -> None:
            s.apply_event(key, HsmEvent.E6_BANKING_OR_WALLET_OPENED, now_ms=now_ms, event_id=eid)
            if result.tier is RiskTier.RED_HARD_INTERCEPTION:
                s.apply_event(
                    key,
                    HsmEvent.G1_CALL_OR_SCREENSHARE_OR_HIGH_RISK,
                    now_ms=now_ms,
                    event_id=f"{eid}:g1",
                )

        await self._store.mutate(device, now_ms, op)
        side.append("BANK_OPEN_EVALUATED")
        side.append(f"RISK_TIER_{result.tier.value}")
        if result.degraded:
            side.append("DEGRADED_FLOOR_APPLIED")
        return True, (result.log_odds, result.probability)
