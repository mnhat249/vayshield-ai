"""Bảng chuyển trạng thái HSM 10 trạng thái (File 2 §2.1) — hàm thuần, không I/O.

``transition()`` KHÔNG đọc đồng hồ: caller truyền ``now_ms`` / ``entered_at_ms``.
Cặp (state, event) không có trong bảng → bị từ chối, giữ nguyên trạng thái (không ném lỗi:
luồng sự kiện thực tế có nhiều sự kiện không liên quan tới một luồng vay cụ thể).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.core.schemas.common import HsmState
from app.shield_state.risk import HOUR_MS


class HsmEvent(StrEnum):
    E1_APK_INSTALLED = "E1_APK_INSTALLED"
    E2_IMAGE_OR_VOICE_TERMS_SUBMITTED = "E2_IMAGE_OR_VOICE_TERMS_SUBMITTED"
    E3_ANY_ORG_CREDIT_INFLOW = "E3_ANY_ORG_CREDIT_INFLOW"
    E4_HIGH_CONF_DISBURSEMENT_NOTIF = "E4_HIGH_CONF_DISBURSEMENT_NOTIF"
    E5_B2B_WEBHOOK = "E5_B2B_WEBHOOK"
    E6_BANKING_OR_WALLET_OPENED = "E6_BANKING_OR_WALLET_OPENED"
    E7_COERCION_DETECTED = "E7_COERCION_DETECTED"
    E9_SMURF_BYPASS_ATTEMPT = "E9_SMURF_BYPASS_ATTEMPT"
    E10_TIMER_DONE_AND_VERIFIED_SAFE = "E10_TIMER_DONE_AND_VERIFIED_SAFE"
    E11_POST_LOSS_RECEIPT_OR_REPORT = "E11_POST_LOSS_RECEIPT_OR_REPORT"
    E11_USER_ALREADY_TRANSFERRED = "E11_USER_ALREADY_TRANSFERRED"
    V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT = "V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT"
    V2_LOAN_TERMS_EXTRACTED = "V2_LOAN_TERMS_EXTRACTED"
    T1_MONOTONIC_TTL_EXPIRED = "T1_MONOTONIC_TTL_EXPIRED"
    T2_SHIELD_24H_COMPLETED = "T2_SHIELD_24H_COMPLETED"
    G1_CALL_OR_SCREENSHARE_OR_HIGH_RISK = "G1_CALL_OR_SCREENSHARE_OR_HIGH_RISK"
    G2_NO_CALL_AND_SAFE_DECLARATION = "G2_NO_CALL_AND_SAFE_DECLARATION"
    # 3 cạnh quay về S4 trong sơ đồ không có mã sự kiện → đặt tên theo nhãn cạnh.
    A_RESUME_REMAINING_WATCH = "A_RESUME_REMAINING_WATCH"  # S8 → S4
    A_MAX_ALERT_RESCUE_LOGGED = "A_MAX_ALERT_RESCUE_LOGGED"  # S7 → S4
    A_TRIAGE_ACTIONS_DONE = "A_TRIAGE_ACTIONS_DONE"  # S9 → S4


class TransitionRejection(StrEnum):
    UNDEFINED = "UNDEFINED"
    GUARD_WINDOW_EXPIRED = "GUARD_WINDOW_EXPIRED"
    GUARD_TTL_NOT_REACHED = "GUARD_TTL_NOT_REACHED"


S = HsmState
E = HsmEvent

TRANSITIONS: Final[Mapping[tuple[HsmState, HsmEvent], HsmState]] = MappingProxyType(
    {
        (S.S0_IDLE, E.E1_APK_INSTALLED): S.S1_LOAN_EXPLORATION,
        (S.S0_IDLE, E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED): S.S2_CONTRACT_AUDITING,
        (S.S0_IDLE, E.E4_HIGH_CONF_DISBURSEMENT_NOTIF): S.S4_SHIELD_ARMED_24H,
        (S.S0_IDLE, E.E5_B2B_WEBHOOK): S.S4_SHIELD_ARMED_24H,
        (S.S0_IDLE, E.E11_POST_LOSS_RECEIPT_OR_REPORT): S.S9_GOLDEN_HOUR_TRIAGE,
        (S.S1_LOAN_EXPLORATION, E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED): S.S2_CONTRACT_AUDITING,
        (S.S1_LOAN_EXPLORATION, E.E3_ANY_ORG_CREDIT_INFLOW): S.S4_SHIELD_ARMED_24H,
        (S.S1_LOAN_EXPLORATION, E.T1_MONOTONIC_TTL_EXPIRED): S.S0_IDLE,
        (S.S2_CONTRACT_AUDITING, E.V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT): (
            S.S3_ADVANCE_FEE_HARD_STOP
        ),
        (S.S2_CONTRACT_AUDITING, E.V2_LOAN_TERMS_EXTRACTED): S.S4_SHIELD_ARMED_24H,
        (S.S3_ADVANCE_FEE_HARD_STOP, E.E6_BANKING_OR_WALLET_OPENED): S.S5_CRITICAL_INTERCEPTION,
        (S.S3_ADVANCE_FEE_HARD_STOP, E.E11_USER_ALREADY_TRANSFERRED): S.S9_GOLDEN_HOUR_TRIAGE,
        (S.S4_SHIELD_ARMED_24H, E.E6_BANKING_OR_WALLET_OPENED): S.S5_CRITICAL_INTERCEPTION,
        (S.S4_SHIELD_ARMED_24H, E.T2_SHIELD_24H_COMPLETED): S.S0_IDLE,
        (S.S5_CRITICAL_INTERCEPTION, E.G1_CALL_OR_SCREENSHARE_OR_HIGH_RISK): S.S6_COOLING_OFF_LOCK,
        (S.S5_CRITICAL_INTERCEPTION, E.G2_NO_CALL_AND_SAFE_DECLARATION): S.S8_SAFE_ROUTINE_TX,
        (S.S6_COOLING_OFF_LOCK, E.E7_COERCION_DETECTED): S.S7_ESCALATED_SCAM_CONFIRMED,
        (S.S6_COOLING_OFF_LOCK, E.E9_SMURF_BYPASS_ATTEMPT): S.S7_ESCALATED_SCAM_CONFIRMED,
        (S.S6_COOLING_OFF_LOCK, E.E11_USER_ALREADY_TRANSFERRED): S.S9_GOLDEN_HOUR_TRIAGE,
        (S.S6_COOLING_OFF_LOCK, E.E10_TIMER_DONE_AND_VERIFIED_SAFE): S.S8_SAFE_ROUTINE_TX,
        (S.S8_SAFE_ROUTINE_TX, E.A_RESUME_REMAINING_WATCH): S.S4_SHIELD_ARMED_24H,
        (S.S7_ESCALATED_SCAM_CONFIRMED, E.A_MAX_ALERT_RESCUE_LOGGED): S.S4_SHIELD_ARMED_24H,
        (S.S9_GOLDEN_HOUR_TRIAGE, E.A_TRIAGE_ACTIONS_DONE): S.S4_SHIELD_ARMED_24H,
    }
)

# Mở rộng an toàn (giả định, ngoài sơ đồ): báo đã mất tiền ở BẤT KỲ trạng thái nào → S9 cấp cứu
# Giờ Vàng. Không bao giờ bỏ rơi nạn nhân chỉ vì luồng đang ở S4/S5/S8...
GLOBAL_TRANSITIONS: Final[Mapping[HsmEvent, HsmState]] = MappingProxyType(
    {
        E.E11_POST_LOSS_RECEIPT_OR_REPORT: S.S9_GOLDEN_HOUR_TRIAGE,
        E.E11_USER_ALREADY_TRANSFERRED: S.S9_GOLDEN_HOUR_TRIAGE,
    }
)

EXPLORATION_TTL_MS: Final = 72 * HOUR_MS  # S1 "Within 72h" / T1
HARD_STOP_BANK_WINDOW_MS: Final = 4 * HOUR_MS  # S3 → S5 "Within 4h"
SHIELD_DEFAULT_MS: Final = 24 * HOUR_MS
PRE_ARM_MS: Final = 72 * HOUR_MS  # V2 "Pre-arm 72h -> 24h on Credit"

# Thời lượng Khiên khi một sự kiện đưa luồng vào S4 (None = giữ hạn hiện có, nếu thiếu → 24h).
SHIELD_ARM_MS: Final[Mapping[HsmEvent, int]] = MappingProxyType(
    {
        E.E3_ANY_ORG_CREDIT_INFLOW: SHIELD_DEFAULT_MS,
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF: SHIELD_DEFAULT_MS,
        E.E5_B2B_WEBHOOK: SHIELD_DEFAULT_MS,
        E.V2_LOAN_TERMS_EXTRACTED: PRE_ARM_MS,
    }
)

# Thứ tự toàn phần theo mức nghiêm trọng → join-semilattice (⊔ = max). Giả định thứ tự: S9 (đã
# mất tiền, cần cấp cứu ngay) > S7 > S6 > S5 > S3 > S4 > S8 > S2 > S1 > S0.
STATE_SEVERITY: Final[Mapping[HsmState, int]] = MappingProxyType(
    {
        S.S0_IDLE: 0,
        S.S1_LOAN_EXPLORATION: 1,
        S.S2_CONTRACT_AUDITING: 2,
        S.S8_SAFE_ROUTINE_TX: 3,
        S.S4_SHIELD_ARMED_24H: 4,
        S.S3_ADVANCE_FEE_HARD_STOP: 5,
        S.S5_CRITICAL_INTERCEPTION: 6,
        S.S6_COOLING_OFF_LOCK: 7,
        S.S7_ESCALATED_SCAM_CONFIRMED: 8,
        S.S9_GOLDEN_HOUR_TRIAGE: 9,
    }
)

# Trạng thái có Khiên đang hiệu lực (khớp partial index File 4 idx_sessions_active_shield).
SHIELDED_STATES: Final = frozenset(
    {
        S.S3_ADVANCE_FEE_HARD_STOP,
        S.S4_SHIELD_ARMED_24H,
        S.S6_COOLING_OFF_LOCK,
        S.S7_ESCALATED_SCAM_CONFIRMED,
        S.S9_GOLDEN_HOUR_TRIAGE,
    }
)


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    from_state: HsmState
    event: HsmEvent
    to_state: HsmState
    accepted: bool
    rejection: TransitionRejection | None = None

    @property
    def changed(self) -> bool:
        return self.accepted and self.to_state is not self.from_state


def join_state(a: HsmState, b: HsmState) -> HsmState:
    """a ⊔ b: trạng thái nghiêm trọng hơn (giao hoán, kết hợp, lũy đẳng)."""
    return a if STATE_SEVERITY[a] >= STATE_SEVERITY[b] else b


def _reject(state: HsmState, event: HsmEvent, why: TransitionRejection) -> TransitionOutcome:
    return TransitionOutcome(state, event, state, accepted=False, rejection=why)


def transition(
    state: HsmState,
    event: HsmEvent,
    *,
    now_ms: int,
    entered_at_ms: int,
    shield_expires_at_ms: int | None = None,
) -> TransitionOutcome:
    """Áp một sự kiện lên trạng thái của MỘT luồng vay (fiber).

    ``entered_at_ms``: thời điểm luồng vào ``state`` hiện tại (cho guard 72h / 4h).
    """
    target = TRANSITIONS.get((state, event))
    if target is None:
        target = GLOBAL_TRANSITIONS.get(event)
        if target is None:
            return _reject(state, event, TransitionRejection.UNDEFINED)
        return TransitionOutcome(state, event, target, accepted=True)

    elapsed = now_ms - entered_at_ms
    if (state, event) == (S.S1_LOAN_EXPLORATION, E.E3_ANY_ORG_CREDIT_INFLOW):
        if elapsed > EXPLORATION_TTL_MS:
            return _reject(state, event, TransitionRejection.GUARD_WINDOW_EXPIRED)
    elif (state, event) == (S.S3_ADVANCE_FEE_HARD_STOP, E.E6_BANKING_OR_WALLET_OPENED):
        if elapsed > HARD_STOP_BANK_WINDOW_MS:
            return _reject(state, event, TransitionRejection.GUARD_WINDOW_EXPIRED)
    elif event is E.T1_MONOTONIC_TTL_EXPIRED:
        if elapsed < EXPLORATION_TTL_MS:
            return _reject(state, event, TransitionRejection.GUARD_TTL_NOT_REACHED)
    elif event is E.T2_SHIELD_24H_COMPLETED:
        expires = (
            shield_expires_at_ms
            if shield_expires_at_ms is not None
            else entered_at_ms + SHIELD_DEFAULT_MS
        )
        if now_ms < expires:
            return _reject(state, event, TransitionRejection.GUARD_TTL_NOT_REACHED)
    return TransitionOutcome(state, event, target, accepted=True)
