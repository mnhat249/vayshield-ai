"""Số học log-odds / P(Scam) theo File 2 §2.2."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.schemas.common import AudioCallState
from app.shield_state.risk import (
    DISB_PLATEAU_MS,
    HOUR_MS,
    L_PRIOR,
    MINUTE_MS,
    AudioMode,
    Evidence,
    EvidenceKind,
    RiskTier,
    SensorContext,
    call_synergy,
    compute_effective_log_odds,
    compute_log_odds,
    evidence_contribution,
    explain_log_odds,
    in_golden_window,
    risk_tier,
    scam_probability,
    sensors_from_call_state,
    smurf_term,
)

NOW = 10_000_000_000


def disb(at: int = NOW, eid: str = "d1") -> Evidence:
    return Evidence(EvidenceKind.DISBURSEMENT, at, eid)


def spoof(at: int = NOW, eid: str = "s1") -> Evidence:
    return Evidence(EvidenceKind.SPOOF_OR_ADVANCE_FEE, at, eid)


def apk(at: int = NOW, eid: str = "a1") -> Evidence:
    return Evidence(EvidenceKind.OFFSTORE_APK, at, eid)


def test_prior_is_about_2_9_percent() -> None:
    assert compute_log_odds([], NOW) == L_PRIOR == -3.5
    assert scam_probability(L_PRIOR) == pytest.approx(0.0293, abs=5e-4)
    assert risk_tier(scam_probability(L_PRIOR)) is RiskTier.PASS_SILENT


def test_disbursement_plus_in_call_is_95_7_percent() -> None:
    """Ví dụ spec: giải ngân + IN_CALL → L = −3.5 + 2.8 + 3.8 = 3.1 → P ≈ 95.7% (Mức Đỏ)."""
    sensors = SensorContext(audio_mode=AudioMode.IN_CALL)
    lo = compute_log_odds([disb()], NOW + 5 * MINUTE_MS, sensors)
    assert lo == pytest.approx(3.1)
    p = scam_probability(lo)
    assert round(p * 100, 1) == 95.7
    assert risk_tier(p) is RiskTier.RED_HARD_INTERCEPTION


def test_in_comm_voip_counts_as_active_call() -> None:
    sensors = SensorContext(audio_mode=AudioMode.IN_COMM)
    assert compute_log_odds([disb()], NOW, sensors) == pytest.approx(3.1)


def test_ec02_recent_call_end_within_180s_adds_1_9() -> None:
    assert call_synergy(
        disbursement_is_active=True, audio_mode=AudioMode.NORMAL, ms_since_call_end=180_000
    ) == pytest.approx(1.9)
    assert (
        call_synergy(
            disbursement_is_active=True, audio_mode=AudioMode.NORMAL, ms_since_call_end=180_001
        )
        == 0.0
    )
    assert (
        call_synergy(
            disbursement_is_active=False, audio_mode=AudioMode.IN_CALL, ms_since_call_end=None
        )
        == 0.0
    )


def test_projection_plus_disbursement_plus_second_open_reaches_99_1_percent() -> None:
    """Ξ = +4.5: giải ngân + chia sẻ màn hình + mở app NH lần 2 → L = 4.7 → P ≈ 99.1%."""
    sensors = SensorContext(media_projection_active=True, bank_open_count_15m=2)
    lo = compute_log_odds([disb()], NOW, sensors)
    assert lo == pytest.approx(4.7)
    assert round(scam_probability(lo) * 100, 1) == 99.1


def test_remote_control_pkg_triggers_xi() -> None:
    b = explain_log_odds([], NOW, SensorContext(remote_control_active=True))
    assert b.xi_proj == pytest.approx(4.5)


@pytest.mark.parametrize(("count", "expected"), [(0, 0.0), (1, 0.0), (2, 0.9), (4, 2.7)])
def test_ec08_smurf_term_arithmetic(count: int, expected: float) -> None:
    """S2-EC08: Ω_smurf = 0.9·max(0, m−1) tăng theo cấp số cộng."""
    assert smurf_term(count) == pytest.approx(expected)


def test_ec08_smurfing_four_transfers_escalate_to_red() -> None:
    """S2-EC08: nhận 20tr rồi mở app NH 4 lần trong 15 phút → Ω = 2.7 → Đỏ kể cả không gọi."""
    lo = compute_log_odds([disb()], NOW, SensorContext(bank_open_count_15m=4))
    assert lo == pytest.approx(-3.5 + 2.8 + 2.7)
    assert risk_tier(scam_probability(lo)) is RiskTier.RED_HARD_INTERCEPTION


def test_disbursement_two_phase_plateau_then_12h_half_life() -> None:
    assert evidence_contribution(EvidenceKind.DISBURSEMENT, 0) == pytest.approx(2.8)
    assert evidence_contribution(EvidenceKind.DISBURSEMENT, DISB_PLATEAU_MS) == pytest.approx(2.8)
    half = evidence_contribution(EvidenceKind.DISBURSEMENT, DISB_PLATEAU_MS + 12 * HOUR_MS)
    assert half == pytest.approx(1.4)


def test_spoof_and_apk_half_lives() -> None:
    assert evidence_contribution(EvidenceKind.SPOOF_OR_ADVANCE_FEE, 4 * HOUR_MS) == pytest.approx(
        2.1
    )
    assert evidence_contribution(EvidenceKind.OFFSTORE_APK, 48 * HOUR_MS) == pytest.approx(0.9)
    assert evidence_contribution(EvidenceKind.OFFSTORE_APK, 96 * HOUR_MS) == pytest.approx(0.45)


def test_negative_age_clamped_to_full_weight() -> None:
    """Đồng hồ lệch: sự kiện ở 'tương lai' không được làm giảm rủi ro."""
    assert evidence_contribution(EvidenceKind.SPOOF_OR_ADVANCE_FEE, -5_000) == pytest.approx(4.2)


def test_ec31_second_phone_call_golden_window_orange_floor() -> None:
    """S2-EC31: kẻ gian gọi máy phụ, máy chính MODE_NORMAL. Trong 120 phút vàng:
    - có cờ Trạm 2 (+4.2): P = σ(0.7) ≈ 66.8% → Mức Cam theo đúng công thức;
    - chỉ giải ngân (+2.8): P = σ(−0.7) ≈ 33.2% < 0.35 → sàn Mức Cam của 120 phút vàng."""
    t = NOW + 90 * MINUTE_MS
    with_flag = scam_probability(compute_log_odds([disb(), spoof()], t))
    assert with_flag > 0.75  # cả 2 bằng chứng: −3.5+2.8+4.2·decay(90') > Đỏ
    only_spoof_weight = scam_probability(L_PRIOR + 4.2)
    assert risk_tier(only_spoof_weight) is RiskTier.ORANGE_SOFT_CHECK
    only_disb = scam_probability(compute_log_odds([disb()], t))
    assert only_disb == pytest.approx(0.3318, abs=1e-3)
    assert in_golden_window([disb()], t)
    assert risk_tier(only_disb) is RiskTier.PASS_SILENT
    assert risk_tier(only_disb, golden_hour_floor=True) is RiskTier.ORANGE_SOFT_CHECK
    # Hết 120 phút vàng → không còn sàn.
    assert not in_golden_window([disb()], NOW + DISB_PLATEAU_MS + 1)


def test_ec30_gamma_multi_only_for_two_or_more_fibers() -> None:
    """S2-EC30: L_eff = max L_j + 0.55·ln|F|."""
    assert compute_effective_log_odds([]) == L_PRIOR
    assert compute_effective_log_odds([-1.0]) == pytest.approx(-1.0)
    assert compute_effective_log_odds([-1.0, 0.5, -3.5, -2.0]) == pytest.approx(
        0.5 + 0.55 * math.log(4)
    )


@pytest.mark.parametrize(
    ("p", "tier"),
    [
        (0.0, RiskTier.PASS_SILENT),
        (0.3499, RiskTier.PASS_SILENT),
        (0.35, RiskTier.ORANGE_SOFT_CHECK),
        (0.7499, RiskTier.ORANGE_SOFT_CHECK),
        (0.75, RiskTier.RED_HARD_INTERCEPTION),
        (1.0, RiskTier.RED_HARD_INTERCEPTION),
    ],
)
def test_thresholds(p: float, tier: RiskTier) -> None:
    assert risk_tier(p) is tier


def test_sensors_from_call_state_mapping() -> None:
    assert sensors_from_call_state(AudioCallState.ACTIVE_CELLULAR).audio_mode is AudioMode.IN_CALL
    assert sensors_from_call_state(AudioCallState.ACTIVE_VOIP).audio_mode is AudioMode.IN_COMM
    ended = sensors_from_call_state(AudioCallState.ENDED_WITHIN_180S)
    assert ended.audio_mode is AudioMode.NORMAL and ended.ms_since_call_end == 0
    assert sensors_from_call_state(AudioCallState.NONE).ms_since_call_end is None


@given(st.floats(min_value=-800, max_value=800, allow_nan=False))
def test_sigmoid_stable_and_bounded(lo: float) -> None:
    p = scam_probability(lo)
    assert 0.0 <= p <= 1.0
    assert scam_probability(-lo) == pytest.approx(1.0 - p, abs=1e-12)


@given(
    st.lists(
        st.tuples(st.sampled_from(list(EvidenceKind)), st.integers(0, 30 * 24 * 3600 * 1000)),
        max_size=10,
    )
)
def test_evidence_never_lowers_below_prior_and_decays_monotonically(
    items: list[tuple[EvidenceKind, int]],
) -> None:
    ev = [Evidence(k, NOW - age, f"e{i}") for i, (k, age) in enumerate(items)]
    lo_now = compute_log_odds(ev, NOW)
    lo_later = compute_log_odds(ev, NOW + HOUR_MS)
    assert lo_now >= L_PRIOR
    assert lo_later <= lo_now + 1e-12
