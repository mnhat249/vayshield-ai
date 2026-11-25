"""Bảng chuyển trạng thái HSM 10 trạng thái (File 2 §2.1) — kiểm đầy đủ mọi cặp (state, event)."""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.schemas.common import HsmState
from app.shield_state.hsm import (
    GLOBAL_TRANSITIONS,
    STATE_SEVERITY,
    TRANSITIONS,
    HsmEvent,
    TransitionRejection,
    join_state,
    transition,
)
from app.shield_state.risk import HOUR_MS

S = HsmState
E = HsmEvent
T0 = 1_000_000_000

# Bảng kỳ vọng chép tay từ sơ đồ mermaid File 2 §2.1 (độc lập với code).
EXPECTED: dict[tuple[HsmState, HsmEvent], HsmState] = {
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


def test_table_matches_spec_diagram_exactly() -> None:
    assert dict(TRANSITIONS) == EXPECTED
    assert len(TRANSITIONS) == 23


def _ok_kwargs(state: HsmState, event: HsmEvent) -> dict[str, int | None]:
    """Ngữ cảnh thời gian làm guard thỏa mãn."""
    if event is E.T1_MONOTONIC_TTL_EXPIRED:
        return {"now_ms": T0 + 72 * HOUR_MS, "entered_at_ms": T0, "shield_expires_at_ms": None}
    if event is E.T2_SHIELD_24H_COMPLETED:
        return {
            "now_ms": T0 + 24 * HOUR_MS,
            "entered_at_ms": T0,
            "shield_expires_at_ms": T0 + 24 * HOUR_MS,
        }
    return {"now_ms": T0 + 1, "entered_at_ms": T0, "shield_expires_at_ms": None}


@pytest.mark.parametrize(
    ("state", "event"), list(itertools.product(list(HsmState), list(HsmEvent)))
)
def test_every_state_event_pair(state: HsmState, event: HsmEvent) -> None:
    """Toàn bộ 10 × |E| cặp: cặp có trong bảng → chuyển đúng; E11 mọi nơi → S9 (mở rộng an toàn);
    còn lại → bị từ chối, giữ nguyên trạng thái."""
    out = transition(state, event, **_ok_kwargs(state, event))  # type: ignore[arg-type]
    if (state, event) in EXPECTED:
        assert out.accepted and out.to_state is EXPECTED[(state, event)]
    elif event in GLOBAL_TRANSITIONS:
        assert out.accepted and out.to_state is S.S9_GOLDEN_HOUR_TRIAGE
    else:
        assert not out.accepted
        assert out.to_state is state
        assert out.rejection is TransitionRejection.UNDEFINED


def test_s1_credit_inflow_only_within_72h() -> None:
    ok = transition(
        S.S1_LOAN_EXPLORATION,
        E.E3_ANY_ORG_CREDIT_INFLOW,
        now_ms=T0 + 72 * HOUR_MS,
        entered_at_ms=T0,
    )
    assert ok.accepted
    late = transition(
        S.S1_LOAN_EXPLORATION,
        E.E3_ANY_ORG_CREDIT_INFLOW,
        now_ms=T0 + 72 * HOUR_MS + 1,
        entered_at_ms=T0,
    )
    assert not late.accepted and late.rejection is TransitionRejection.GUARD_WINDOW_EXPIRED


def test_s1_ttl_not_reached() -> None:
    out = transition(
        S.S1_LOAN_EXPLORATION,
        E.T1_MONOTONIC_TTL_EXPIRED,
        now_ms=T0 + 71 * HOUR_MS,
        entered_at_ms=T0,
    )
    assert not out.accepted and out.rejection is TransitionRejection.GUARD_TTL_NOT_REACHED


def test_s3_banking_opened_within_4h_only() -> None:
    ok = transition(
        S.S3_ADVANCE_FEE_HARD_STOP,
        E.E6_BANKING_OR_WALLET_OPENED,
        now_ms=T0 + 4 * HOUR_MS,
        entered_at_ms=T0,
    )
    assert ok.to_state is S.S5_CRITICAL_INTERCEPTION
    late = transition(
        S.S3_ADVANCE_FEE_HARD_STOP,
        E.E6_BANKING_OR_WALLET_OPENED,
        now_ms=T0 + 5 * HOUR_MS,
        entered_at_ms=T0,
    )
    # Giữ nguyên S3 (vẫn là trạng thái chặn cứng) — không bao giờ rơi về trạng thái kém an toàn.
    assert not late.accepted and late.to_state is S.S3_ADVANCE_FEE_HARD_STOP


def test_s4_shield_completion_requires_expiry() -> None:
    early = transition(
        S.S4_SHIELD_ARMED_24H,
        E.T2_SHIELD_24H_COMPLETED,
        now_ms=T0 + HOUR_MS,
        entered_at_ms=T0,
        shield_expires_at_ms=T0 + 24 * HOUR_MS,
    )
    assert not early.accepted and early.rejection is TransitionRejection.GUARD_TTL_NOT_REACHED
    missing = transition(
        S.S4_SHIELD_ARMED_24H, E.T2_SHIELD_24H_COMPLETED, now_ms=T0 + 99 * HOUR_MS, entered_at_ms=T0
    )
    # Không biết hạn Khiên → coi như 24h kể từ lúc vào S4.
    assert missing.accepted


def test_severity_is_total_order_over_10_states() -> None:
    assert set(STATE_SEVERITY) == set(HsmState)
    assert len(set(STATE_SEVERITY.values())) == 10
    assert STATE_SEVERITY[S.S0_IDLE] == min(STATE_SEVERITY.values())


states = st.sampled_from(list(HsmState))


@given(states, states, states)
def test_join_state_is_semilattice(a: HsmState, b: HsmState, c: HsmState) -> None:
    assert join_state(a, a) is a
    assert join_state(a, b) is join_state(b, a)
    assert join_state(join_state(a, b), c) is join_state(a, join_state(b, c))
    assert join_state(S.S0_IDLE, a) is a
