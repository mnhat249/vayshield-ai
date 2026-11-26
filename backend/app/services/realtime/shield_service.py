"""Logic Trạm 3: kích hoạt Khiên, đánh giá câu trả lời 10 trục, leo thang (File 4 §4.1, File 2 §2).

Nối ``ShieldStateStore`` (T05) + bộ phân loại ontology (T07) + ``EscalationService``. Không import
``app.verdict`` / ``ai_pipeline.extraction`` (chưa merge).

Quyết định an toàn:
- ``arm_shield`` trả ``None`` (E3 độ tin cậy thấp + chưa có hành trình vay) là KẾT QUẢ TƯỜNG MINH:
  route trả 409 ``CONFLICT`` kèm ``details[0].issue = NO_LOAN_JOURNEY`` — không bao giờ giả vờ đã
  bật Khiên, cũng không 500. (``ShieldStateStore.arm_shield`` đã ghi log ``shield_arm_rejected``,
  không có device_id.)
- Store ở chế độ ``degraded`` (Redis/PG không xác nhận được) → đánh giá có SÀN Mức Cam: PASS bị
  nâng thành ORANGE_RED.
- Thời gian lưu S5/S6 ≥ 30 phút, S7/S9 ≥ 24h (``INTERCEPT_MIN_DWELL_MS``/``RESCUE_MIN_DWELL_MS``)
  do store tự quản; route không giả định trạng thái "tự hết" nhanh hơn.
"""

from __future__ import annotations

import hashlib
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from ai_pipeline.ontology import classify_response, generate_challenge
from app.core.clock import Clock
from app.core.errors import AppError, ErrorCode, ErrorDetail
from app.core.logging import get_logger
from app.core.schemas.common import HsmState, OntologyAction, TriggerOrigin
from app.core.schemas.shield import (
    EscalateRequest,
    EscalateResponse,
    EscalationReason,
    EvaluateResponseRequest,
    EvaluateResponseResult,
    ShieldArmRequest,
    ShieldArmResponse,
)
from app.services.realtime.escalation import EscalationService
from app.services.realtime.models import BorrowerPrincipal
from app.services.realtime.persistence import ShieldPersistence
from app.shield_state.fibers import SOURCE_FROM_ORIGIN, FiberSupervisor
from app.shield_state.hsm import HsmEvent
from app.shield_state.idempotency import claim_idempotency, claim_notification
from app.shield_state.keys import notif_idem_key
from app.shield_state.store import DeviceShieldSnapshot, ShieldStateStore

logger = get_logger("vayshield.realtime.shield")

HOUR_MS: Final = 3_600_000
ARM_IDEM_TTL_MS: Final = 3_600_000

EVENT_BY_ORIGIN: Final[dict[TriggerOrigin, HsmEvent]] = {
    TriggerOrigin.STATION_3_NOTIF: HsmEvent.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
    TriggerOrigin.B2B_PARTNER_WEBHOOK: HsmEvent.E5_B2B_WEBHOOK,
    TriggerOrigin.SANDBOX_SIM: HsmEvent.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
    # Trạm 1/2: tín hiệu tiền về có độ tin cậy thấp (E3), cần có hành trình vay trước đó.
    TriggerOrigin.STATION_1_APK: HsmEvent.E3_ANY_ORG_CREDIT_INFLOW,
    TriggerOrigin.STATION_2_SCAN: HsmEvent.E3_ANY_ORG_CREDIT_INFLOW,
    TriggerOrigin.STATION_2_VOICE: HsmEvent.E3_ANY_ORG_CREDIT_INFLOW,
}
EVENT_BY_REASON: Final[dict[EscalationReason, HsmEvent]] = {
    EscalationReason.COERCION_DETECTED: HsmEvent.E7_COERCION_DETECTED,
    EscalationReason.USER_CONFIRMED_SCAM: HsmEvent.E7_COERCION_DETECTED,
    EscalationReason.SMURF_BYPASS_ATTEMPT: HsmEvent.E9_SMURF_BYPASS_ATTEMPT,
    EscalationReason.USER_ALREADY_TRANSFERRED: HsmEvent.E11_USER_ALREADY_TRANSFERRED,
}

DEGRADED_MESSAGE_VI: Final = (
    "Hệ thống đang chạy chế độ hạ cấp nên chưa xác nhận được trạng thái bảo vệ. "
    "Hãy thận trọng, kiểm tra kỹ người nhận tiền và gọi người thân nếu còn nghi ngờ."
)
DEGRADED_TTS_VI: Final = "Hãy thận trọng. Hệ thống đang hạ cấp, hãy kiểm tra kỹ người nhận tiền."
DEGRADED_FLOOR_PROB: Final = 0.35


def _ms(clock: Clock) -> int:
    return int(clock.now().timestamp() * 1000)


class ShieldService:
    def __init__(
        self,
        *,
        redis: Redis,
        clock: Clock,
        store: ShieldStateStore,
        escalation: EscalationService,
        persistence: ShieldPersistence,
    ) -> None:
        self._redis = redis
        self._clock = clock
        self._store = store
        self._escalation = escalation
        self._persistence = persistence

    # --- POST /shield/arm ------------------------------------------------------------------
    async def arm(self, who: BorrowerPrincipal, body: ShieldArmRequest) -> ShieldArmResponse:
        device = str(who.device_id)
        now_ms = _ms(self._clock)
        event_hash = hashlib.sha256(body.client_event_id.encode()).hexdigest()[:32]
        idem_key = f"vs:idem:arm:{device}:{event_hash}"
        first = await claim_idempotency(
            self._redis, idem_key, body.client_event_id, ARM_IDEM_TTL_MS
        )
        notif_first = True
        owns_notif = False
        if body.notif_dedupe_hash is not None:
            # S2-EC07: SMS Banking + Push app ngân hàng cùng 1 khoản → chỉ 1 phiên.
            claim = await claim_notification(
                self._redis, device, body.notif_dedupe_hash, body.client_event_id
            )
            notif_first = claim.first or claim.owner == body.client_event_id
            owns_notif = claim.first
        if not first.first or not notif_first:
            return await self._deduplicated(who, body, now_ms)

        async def release() -> None:
            # Chỉ nhả khóa do CHÍNH request này giữ; retry cùng client_event_id phải arm được.
            if first.first:
                await self._redis.delete(idem_key)
            if owns_notif and body.notif_dedupe_hash is not None:
                await self._redis.delete(notif_idem_key(device, body.notif_dedupe_hash))

        try:
            snap, fiber = await self._store.arm_shield(
                device,
                body.fiber_key,
                EVENT_BY_ORIGIN[body.trigger_origin],
                now_ms=now_ms,
                event_id=body.client_event_id,
                source=SOURCE_FROM_ORIGIN[body.trigger_origin],
                shield_ms=body.shield_duration_hours * HOUR_MS,
            )
            if fiber is None:
                await release()
                raise AppError(
                    ErrorCode.CONFLICT,
                    409,
                    "Chưa có khoản vay nào để theo dõi. Hãy kiểm tra hợp đồng vay trước.",
                    details=[ErrorDetail(field="trigger_origin", issue="NO_LOAN_JOURNEY")],
                )
            await self._persist(who, snap, now_ms, body)
        except BaseException:
            # 503 degraded / 409 CAS / hủy request: không để khóa idempotency chặn lần thử lại.
            await release()
            raise
        return self._arm_response(
            fiber.session_id, fiber.fiber_key, fiber.state, snap, now_ms, False
        )

    async def _deduplicated(
        self, who: BorrowerPrincipal, body: ShieldArmRequest, now_ms: int
    ) -> ShieldArmResponse:
        snap = await self._store.load(str(who.device_id), now_ms)
        sup = FiberSupervisor(snap.fibers)
        fiber = sup.get(body.fiber_key)
        if fiber is None:
            active = sup.active_fibers()
            fiber = active[0] if active else None
        if fiber is None:
            raise AppError(
                ErrorCode.CONFLICT,
                409,
                "Chưa có khoản vay nào để theo dõi.",
                details=[ErrorDetail(field="trigger_origin", issue="NO_LOAN_JOURNEY")],
            )
        return self._arm_response(
            fiber.session_id, fiber.fiber_key, fiber.state, snap, now_ms, True
        )

    @staticmethod
    def _arm_response(
        session_id: str,
        fiber_key: str,
        state: HsmState,
        snap: DeviceShieldSnapshot,
        now_ms: int,
        deduplicated: bool,
    ) -> ShieldArmResponse:
        from datetime import UTC, datetime

        sup = FiberSupervisor(snap.fibers)
        fiber = sup.get(fiber_key)
        expires_ms = (fiber.shield_expires_at_ms if fiber else None) or sup.shield_expires_at_ms()
        expires = datetime.fromtimestamp((expires_ms or now_ms) / 1000, tz=UTC)
        return ShieldArmResponse(
            session_id=UUID(session_id),
            fiber_key=fiber_key,
            hsm_state=state,
            shield_expires_at=expires,
            risk_log_odds=sup.base_log_odds(now_ms),
            deduplicated=deduplicated,
        )

    async def _persist(
        self,
        who: BorrowerPrincipal,
        snap: DeviceShieldSnapshot,
        now_ms: int,
        body: ShieldArmRequest | None = None,
    ) -> None:
        sup = FiberSupervisor(snap.fibers)
        await self._persistence.save(
            who.device_id,
            snap.fibers,
            sandbox_namespace=who.sandbox_namespace,
            trigger_origin=None if body is None else body.trigger_origin,
            amount_bucket_tier=None if body is None else body.amount_bucket_tier,
            disbursement_at=None if body is None else body.disbursement_at,
            risk_log_odds=sup.base_log_odds(now_ms),
        )

    # --- POST /shield/evaluate-response ----------------------------------------------------------
    async def evaluate(
        self, who: BorrowerPrincipal, body: EvaluateResponseRequest
    ) -> EvaluateResponseResult:
        device = str(who.device_id)
        sid = str(body.session_id)
        now_ms = _ms(self._clock)
        snap = await self._store.load(device, now_ms)
        known = any(f.session_id == sid for f in snap.fibers)
        if not known and not snap.degraded:
            raise AppError(ErrorCode.NOT_FOUND, 404)

        result = classify_response(
            text=body.utterance_text,
            choice_id=body.choice_id,
            sensors=body.sensors,
            llm=None,  # chưa có adapter LLM thật; luật tất định luôn chạy (LLM không hạ mức, D2)
        )
        action = result.action
        message_vi, tts_vi = result.message_vi, result.tts_script_vi
        prob = result.computed_scam_prob
        if action is OntologyAction.PASS and snap.degraded:
            action = OntologyAction.ORANGE_RED
            message_vi, tts_vi = DEGRADED_MESSAGE_VI, DEGRADED_TTS_VI
            prob = max(prob, DEGRADED_FLOOR_PROB)

        next_state = snap.state
        if known:
            next_state = await self._advance_hsm(
                who, snap, sid, action, result.detected_axes, now_ms
            )

        follow_up = None
        if action is OntologyAction.ORANGE_RED:
            follow_up = generate_challenge(f"{sid}:{body.challenge_id}")
        return EvaluateResponseResult(
            session_id=body.session_id,
            action=action,
            detected_axes=result.detected_axes,
            computed_scam_prob=prob,
            next_hsm_state=next_state,
            message_vi=message_vi,
            tts_script_vi=tts_vi,
            callback_hotline_hint_vi=result.callback_hotline_hint_vi,
            escalate_recommended=result.escalate_recommended or action is OntologyAction.RED,
            follow_up_challenge=follow_up,
        )

    async def _advance_hsm(
        self,
        who: BorrowerPrincipal,
        snap: DeviceShieldSnapshot,
        session_id: str,
        action: OntologyAction,
        axes: list,  # type: ignore[type-arg]
        now_ms: int,
    ) -> HsmState:
        coercion = action is OntologyAction.RED and bool(axes)

        def op(sup: FiberSupervisor) -> HsmState:
            fiber = sup.by_session(session_id)
            if fiber is None:
                return snap.state
            key = fiber.fiber_key
            eid = f"eval:{session_id}"
            if fiber.state is HsmState.S4_SHIELD_ARMED_24H:
                sup.apply_event(
                    key, HsmEvent.E6_BANKING_OR_WALLET_OPENED, now_ms=now_ms, event_id=f"{eid}:e6"
                )
            if action is OntologyAction.PASS:
                sup.apply_event(
                    key,
                    HsmEvent.G2_NO_CALL_AND_SAFE_DECLARATION,
                    now_ms=now_ms,
                    event_id=f"{eid}:g2",
                )
            else:
                sup.apply_event(
                    key,
                    HsmEvent.G1_CALL_OR_SCREENSHARE_OR_HIGH_RISK,
                    now_ms=now_ms,
                    event_id=f"{eid}:g1",
                )
                if coercion:
                    sup.apply_event(
                        key, HsmEvent.E7_COERCION_DETECTED, now_ms=now_ms, event_id=f"{eid}:e7"
                    )
            after = sup.get(key)
            return after.state if after is not None else snap.state

        new_snap, state = await self._store.mutate(str(who.device_id), now_ms, op)
        await self._persist(who, new_snap, now_ms)
        return state

    # --- POST /shield/escalate -------------------------------------------------------------------
    async def escalate(self, who: BorrowerPrincipal, body: EscalateRequest) -> EscalateResponse:
        now_ms = _ms(self._clock)
        sid = str(body.session_id)
        snap = await self._store.load(str(who.device_id), now_ms)
        known = any(f.session_id == sid for f in snap.fibers)
        if not known and not snap.degraded:
            raise AppError(ErrorCode.NOT_FOUND, 404)
        # Gửi cảnh báo TRƯỚC (an toàn người dùng), cập nhật HSM sau (best-effort).
        resp = await self._escalation.escalate(who.device_id, body)
        if known:
            event = EVENT_BY_REASON[body.reason]
            try:

                def op(sup: FiberSupervisor) -> None:
                    fiber = sup.by_session(sid)
                    if fiber is not None:
                        sup.apply_event(
                            fiber.fiber_key,
                            event,
                            now_ms=now_ms,
                            event_id=f"esc:{body.client_event_id}",
                        )

                new_snap, _ = await self._store.mutate(str(who.device_id), now_ms, op)
                await self._persist(who, new_snap, now_ms)
            except AppError:
                logger.warning("escalate_hsm_update_skipped")
        return resp
