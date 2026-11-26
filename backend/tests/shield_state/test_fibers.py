"""FiberSupervisor K=8 luồng + join-semilattice (File 2 §2.1, S2-EC30, S2-EC43)."""

from __future__ import annotations

import itertools
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.schemas.common import HsmState
from app.shield_state.fibers import (
    DEFAULT_FIBER_KEY,
    K_MAX_FIBERS,
    MAX_EVIDENCE_PER_FIBER,
    EvidenceSource,
    FiberSupervisor,
    LoanJourneyFiber,
    join_fibers,
)
from app.shield_state.hsm import HsmEvent
from app.shield_state.risk import (
    HOUR_MS,
    MINUTE_MS,
    AudioMode,
    Evidence,
    EvidenceKind,
    RiskTier,
    SensorContext,
)

E = HsmEvent
S = HsmState
T0 = 2_000_000_000_000


def make_sup() -> FiberSupervisor:
    counter = itertools.count()
    return FiberSupervisor(new_session_id=lambda: f"sess-{next(counter):04d}")


def test_ec30_four_loan_apps_in_one_hour_do_not_overwrite_each_other() -> None:
    """S2-EC30: App B giải ngân (Khiên 24h) không bị App C/D ghi đè; L_eff = max + γ·ln|F|."""
    sup = make_sup()
    sup.apply_event("appA", E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, now_ms=T0, event_id="a1")
    sup.apply_event(
        "appA", E.V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT, now_ms=T0 + 1, event_id="a2"
    )
    sup.arm_disbursement(
        "appB",
        E.E5_B2B_WEBHOOK,
        now_ms=T0 + 10 * MINUTE_MS,
        event_id="b1",
        source=EvidenceSource.B2B_CONFIRMED,
    )
    sup.apply_event("appC", E.E1_APK_INSTALLED, now_ms=T0 + 20 * MINUTE_MS, event_id="c1")
    sup.apply_event("appD", E.E1_APK_INSTALLED, now_ms=T0 + 30 * MINUTE_MS, event_id="d1")

    states = {f.fiber_key: f.state for f in sup.fibers}
    assert states == {
        "appA": S.S3_ADVANCE_FEE_HARD_STOP,
        "appB": S.S4_SHIELD_ARMED_24H,
        "appC": S.S1_LOAN_EXPLORATION,
        "appD": S.S1_LOAN_EXPLORATION,
    }
    assert sup.get("appB") is not None
    assert sup.get("appB").shield_expires_at_ms == T0 + 10 * MINUTE_MS + 24 * HOUR_MS  # type: ignore[union-attr]
    assert sup.device_state() is S.S3_ADVANCE_FEE_HARD_STOP

    now = T0 + 40 * MINUTE_MS
    ev = sup.evaluate(now, SensorContext())
    per_fiber = sup.fiber_log_odds(now, SensorContext())
    assert len(per_fiber) == 4
    assert ev.log_odds == pytest.approx(max(per_fiber.values()) + 0.55 * math.log(4))
    assert ev.active_fiber_count == 4


def test_k8_overflow_folds_into_least_severe_fiber_without_losing_shield() -> None:
    sup = make_sup()
    sup.arm_disbursement(
        "armed",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="x",
        source=EvidenceSource.EDGE_NOTIF,
    )
    for i in range(K_MAX_FIBERS - 1):
        sup.apply_event(f"apk{i}", E.E1_APK_INSTALLED, now_ms=T0 + i + 1, event_id=f"apk{i}")
    assert len(sup.fibers) == K_MAX_FIBERS
    sup.apply_event("apk_new", E.E1_APK_INSTALLED, now_ms=T0 + 100, event_id="apk_new")
    assert len(sup.fibers) == K_MAX_FIBERS
    armed = sup.get("armed")
    assert armed is not None and armed.state is S.S4_SHIELD_ARMED_24H
    # Luồng ít nghiêm trọng + cũ nhất (apk0) hấp thụ bằng chứng mới.
    apk0 = sup.get("apk0")
    assert apk0 is not None
    assert {e.event_id for e in apk0.evidence} == {"apk0", "apk_new"}


def test_ec43_b2b_and_edge_notif_same_second_merge_into_one_session() -> None:
    """S2-EC43 (phần thuần): webhook B2B + thông báo trên máy cùng giây → 1 luồng, MAX hạn Khiên,
    gộp nguồn bằng chứng."""
    sup = make_sup()
    sup.arm_disbursement(
        DEFAULT_FIBER_KEY,
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="notif-1",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.arm_disbursement(
        "fintechX:loan9",
        E.E5_B2B_WEBHOOK,
        now_ms=T0 + 400,
        event_id="b2b-1",
        source=EvidenceSource.B2B_CONFIRMED,
        shield_ms=48 * HOUR_MS,
    )
    assert len(sup.fibers) == 1
    f = sup.fibers[0]
    assert f.fiber_key == "fintechX:loan9"  # khóa cụ thể hơn thắng
    assert f.evidence_sources == frozenset(
        {EvidenceSource.B2B_CONFIRMED, EvidenceSource.EDGE_NOTIF}
    )
    assert f.shield_expires_at_ms == T0 + 400 + 48 * HOUR_MS
    assert f.state is S.S4_SHIELD_ARMED_24H


def test_ec43_two_distinct_partners_stay_separate() -> None:
    sup = make_sup()
    for key in ("p1:l1", "p2:l2"):
        sup.arm_disbursement(
            key, E.E5_B2B_WEBHOOK, now_ms=T0, event_id=key, source=EvidenceSource.B2B_CONFIRMED
        )
    assert len(sup.fibers) == 2


def test_ec43_outside_30m_window_creates_new_fiber() -> None:
    sup = make_sup()
    sup.arm_disbursement(
        DEFAULT_FIBER_KEY,
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="n",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.arm_disbursement(
        "p1:l1",
        E.E5_B2B_WEBHOOK,
        now_ms=T0 + 31 * MINUTE_MS,
        event_id="b",
        source=EvidenceSource.B2B_CONFIRMED,
    )
    assert len(sup.fibers) == 2


def test_rearm_takes_max_shield_expiry_never_shortens() -> None:
    sup = make_sup()
    sup.apply_event("k", E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, now_ms=T0, event_id="1")
    sup.apply_event("k", E.V2_LOAN_TERMS_EXTRACTED, now_ms=T0, event_id="2")
    pre = sup.get("k")
    assert pre is not None and pre.shield_expires_at_ms == T0 + 72 * HOUR_MS
    sup.arm_disbursement(
        "k",
        E.E3_ANY_ORG_CREDIT_INFLOW,
        now_ms=T0 + HOUR_MS,
        event_id="3",
        source=EvidenceSource.EDGE_NOTIF,
    )
    after = sup.get("k")
    assert after is not None and after.shield_expires_at_ms == T0 + 72 * HOUR_MS


def test_bank_open_broadcast_moves_armed_fibers_to_interception() -> None:
    sup = make_sup()
    sup.arm_disbursement(
        "b",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="b",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.apply_event("c", E.E1_APK_INSTALLED, now_ms=T0, event_id="c")
    outs = sup.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + MINUTE_MS)
    assert [o.to_state for o in outs if o.accepted] == [S.S5_CRITICAL_INTERCEPTION]
    assert sup.get("c").state is S.S1_LOAN_EXPLORATION  # type: ignore[union-attr]


def test_expire_ttl_removes_idle_fibers() -> None:
    sup = make_sup()
    sup.apply_event("c", E.E1_APK_INSTALLED, now_ms=T0, event_id="c")
    sup.arm_disbursement(
        "b",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="b",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.expire(T0 + 24 * HOUR_MS)
    assert [f.fiber_key for f in sup.fibers] == ["c"]
    sup.expire(T0 + 72 * HOUR_MS)
    assert sup.fibers == ()
    assert sup.device_state() is S.S0_IDLE


def test_evaluate_disbursement_in_call_red_95_7() -> None:
    sup = make_sup()
    sup.arm_disbursement(
        "b",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="b",
        source=EvidenceSource.EDGE_NOTIF,
    )
    ev = sup.evaluate(T0 + MINUTE_MS, SensorContext(audio_mode=AudioMode.IN_CALL))
    assert round(ev.probability * 100, 1) == 95.7
    assert ev.tier is RiskTier.RED_HARD_INTERCEPTION


def test_ec31_supervisor_golden_window_orange_floor() -> None:
    sup = make_sup()
    sup.arm_disbursement(
        "b",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="b",
        source=EvidenceSource.EDGE_NOTIF,
    )
    ev = sup.evaluate(T0 + 60 * MINUTE_MS, SensorContext())
    assert ev.probability < 0.35
    assert ev.golden_window and ev.tier is RiskTier.ORANGE_SOFT_CHECK


def test_no_fibers_still_counts_screen_share() -> None:
    ev = make_sup().evaluate(T0, SensorContext(media_projection_active=True))
    assert ev.log_odds == pytest.approx(1.0)


def test_base_log_odds_plus_sensor_terms_equals_full_formula() -> None:
    """Tách nền (Python) + Ψ/Ξ/Ω (Lua) phải bằng công thức đầy đủ."""
    sup = make_sup()
    sup.arm_disbursement(
        "b",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="b",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.apply_event("c", E.E1_APK_INSTALLED, now_ms=T0, event_id="c")
    now = T0 + 3 * HOUR_MS
    sensors = SensorContext(audio_mode=AudioMode.IN_CALL, bank_open_count_15m=3)
    full = sup.evaluate(now, sensors).log_odds
    assert sup.base_log_odds(now) + 3.8 + 1.8 == pytest.approx(full)


def test_fiber_roundtrip_dict() -> None:
    sup = make_sup()
    sup.arm_disbursement(
        "b",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="b",
        source=EvidenceSource.EDGE_NOTIF,
    )
    f = sup.fibers[0]
    assert LoanJourneyFiber.from_dict(f.to_dict()) == f


def test_evidence_capped_per_fiber() -> None:
    sup = make_sup()
    sup.apply_event("c", E.E1_APK_INSTALLED, now_ms=T0, event_id="c0")
    for i in range(1, 50):
        sup.apply_event("c", E.E1_APK_INSTALLED, now_ms=T0 + i, event_id=f"c{i}")
    f = sup.get("c")
    assert f is not None and len(f.evidence) == MAX_EVIDENCE_PER_FIBER
    assert max(e.at_ms for e in f.evidence) == T0 + 49


# --- Join-semilattice trên LoanJourneyFiber -----------------------------------------------------

_ev = st.builds(
    Evidence,
    kind=st.sampled_from(list(EvidenceKind)),
    at_ms=st.integers(0, 10_000),
    event_id=st.sampled_from([f"e{i}" for i in range(6)]),
)
fibers = st.builds(
    LoanJourneyFiber,
    fiber_key=st.just("k"),
    session_id=st.sampled_from(["s1", "s2", "s3"]),
    state=st.sampled_from(list(HsmState)),
    entered_at_ms=st.integers(0, 10_000),
    updated_at_ms=st.integers(0, 10_000),
    shield_expires_at_ms=st.one_of(st.none(), st.integers(0, 10_000)),
    evidence=st.lists(_ev, max_size=5).map(lambda xs: tuple(sorted(set(xs), key=repr))),
    evidence_sources=st.frozensets(st.sampled_from(list(EvidenceSource))),
)


def _canon(f: LoanJourneyFiber) -> LoanJourneyFiber:
    return join_fibers(f, f)


@given(fibers, fibers, fibers)
def test_join_fibers_semilattice_laws(
    a: LoanJourneyFiber, b: LoanJourneyFiber, c: LoanJourneyFiber
) -> None:
    ab = join_fibers(a, b)
    assert ab == join_fibers(b, a)
    assert join_fibers(ab, c) == join_fibers(a, join_fibers(b, c))
    assert join_fibers(ab, ab) == ab
    assert join_fibers(_canon(a), _canon(a)) == _canon(a)


@given(fibers, fibers)
def test_join_never_loses_risk(a: LoanJourneyFiber, b: LoanJourneyFiber) -> None:
    j = join_fibers(a, b)
    exp = [x for x in (a.shield_expires_at_ms, b.shield_expires_at_ms) if x is not None]
    assert j.shield_expires_at_ms == (max(exp) if exp else None)
    assert j.evidence_sources == a.evidence_sources | b.evidence_sources
    assert {e.event_id for e in j.evidence} == {e.event_id for e in a.evidence + b.evidence}


def test_join_rejects_different_keys() -> None:
    a = LoanJourneyFiber("a", "s", S.S0_IDLE, 0, 0)
    with pytest.raises(ValueError, match="fiber_key"):
        join_fibers(a, LoanJourneyFiber("b", "s", S.S0_IDLE, 0, 0))


# --- Review fixes: S2-EC43 không cộng 2 lần, S8 resume, expire mọi trạng thái, tiền về ở S1/S2 ----


def test_ec43_b2b_then_edge_notif_counts_disbursement_once() -> None:
    """S2-EC43: webhook B2B (khóa cụ thể) rồi thông báo trên máy (default) cho CÙNG một khoản →
    1 bằng chứng, L = −3.5 + 2.8 = −0.7, P ≈ 33.2% → Cam nhờ sàn 120 phút vàng (không phải Đỏ)."""
    sup = make_sup()
    sup.arm_disbursement(
        "loan-A",
        E.E5_B2B_WEBHOOK,
        now_ms=T0,
        event_id="b2b-1",
        source=EvidenceSource.B2B_CONFIRMED,
    )
    merged = sup.arm_disbursement(
        DEFAULT_FIBER_KEY,
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0 + 1000,
        event_id="notif-1",
        source=EvidenceSource.EDGE_NOTIF,
    )
    assert merged is not None and merged.fiber_key == "loan-A"
    assert len(sup.fibers) == 1
    f = sup.fibers[0]
    assert [e.event_id for e in f.evidence] == ["b2b-1"]
    assert f.evidence_sources == {EvidenceSource.B2B_CONFIRMED, EvidenceSource.EDGE_NOTIF}
    assert f.shield_expires_at_ms == T0 + 1000 + 24 * HOUR_MS  # MAX
    ev = sup.evaluate(T0 + 2000, SensorContext())
    assert ev.log_odds == pytest.approx(-0.7)
    assert ev.probability == pytest.approx(1 / (1 + math.exp(0.7)))
    assert round(ev.probability * 100, 1) == 33.2
    assert ev.tier is RiskTier.ORANGE_SOFT_CHECK


def test_ec43_notif_then_b2b_counts_once_and_takes_max_expiry() -> None:
    sup = make_sup()
    sup.arm_disbursement(
        DEFAULT_FIBER_KEY,
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="n",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.arm_disbursement(
        "p:l",
        E.E5_B2B_WEBHOOK,
        now_ms=T0 + 500,
        event_id="b",
        source=EvidenceSource.B2B_CONFIRMED,
        shield_ms=48 * HOUR_MS,
    )
    f = sup.fibers[0]
    assert len(f.evidence) == 1
    assert f.shield_expires_at_ms == T0 + 500 + 48 * HOUR_MS
    assert sup.base_log_odds(T0 + 600) == pytest.approx(-0.7)


def test_same_source_second_disbursement_still_counts() -> None:
    """Hai thông báo cùng nguồn = hai khoản khác nhau (khử trùng do idempotency 10m lo)."""
    sup = make_sup()
    for i in range(2):
        sup.arm_disbursement(
            DEFAULT_FIBER_KEY,
            E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
            now_ms=T0 + i,
            event_id=f"n{i}",
            source=EvidenceSource.EDGE_NOTIF,
        )
    assert len(sup.fibers[0].evidence) == 2


def _to_s8(sup: FiberSupervisor) -> None:
    sup.arm_disbursement(
        "y",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="d",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + 10)
    out = sup.apply_event("y", E.G2_NO_CALL_AND_SAFE_DECLARATION, now_ms=T0 + 20, event_id="g2")
    assert out.accepted and out.to_state is S.S8_SAFE_ROUTINE_TX


def test_s8_reopen_bank_resumes_watch_and_intercepts() -> None:
    """S4→E6→S5→G2→S8; mở lại app ngân hàng: S8 resume về S4 rồi E6 → S5 (không UNDEFINED)."""
    sup = make_sup()
    _to_s8(sup)
    outs = sup.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + 30)
    assert len(outs) == 1 and outs[0].accepted and outs[0].rejection is None
    assert outs[0].from_state is S.S4_SHIELD_ARMED_24H
    f = sup.get("y")
    assert f is not None and f.state is S.S5_CRITICAL_INTERCEPTION
    assert f.shield_expires_at_ms == T0 + 24 * HOUR_MS  # hạn Khiên giữ nguyên


def test_s8_apply_event_e6_also_resumes() -> None:
    sup = make_sup()
    _to_s8(sup)
    out = sup.apply_event("y", E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + 30, event_id="o")
    assert out.accepted and out.to_state is S.S5_CRITICAL_INTERCEPTION


def test_s8_is_cleaned_after_shield_expiry_25h() -> None:
    sup = make_sup()
    _to_s8(sup)
    outs = sup.expire(T0 + 25 * HOUR_MS)
    assert [o.to_state for o in outs] == [S.S0_IDLE]
    assert sup.fibers == ()


def test_s8_reopen_after_expiry_does_not_resume() -> None:
    sup = make_sup()
    _to_s8(sup)
    outs = sup.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + 25 * HOUR_MS)
    assert outs == [] and sup.fibers == ()


@pytest.mark.parametrize(
    ("event", "state"),
    [
        (E.A_MAX_ALERT_RESCUE_LOGGED, S.S7_ESCALATED_SCAM_CONFIRMED),
        (E.A_TRIAGE_ACTIONS_DONE, S.S9_GOLDEN_HOUR_TRIAGE),
    ],
)
def test_s7_s9_return_to_s4_via_explicit_action(event: HsmEvent, state: HsmState) -> None:
    sup = make_sup()
    sup.arm_disbursement(
        "z",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0,
        event_id="d",
        source=EvidenceSource.EDGE_NOTIF,
    )
    sup.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + 1)
    sup.apply_event("z", E.G1_CALL_OR_SCREENSHARE_OR_HIGH_RISK, now_ms=T0 + 2, event_id="g1")
    if state is S.S7_ESCALATED_SCAM_CONFIRMED:
        sup.apply_event("z", E.E7_COERCION_DETECTED, now_ms=T0 + 3, event_id="e7")
    else:
        sup.apply_event("z", E.E11_USER_ALREADY_TRANSFERRED, now_ms=T0 + 3, event_id="e11")
    f = sup.get("z")
    assert f is not None and f.state is state
    out = sup.apply_event("z", event, now_ms=T0 + 4, event_id="a")
    assert out.accepted and out.to_state is S.S4_SHIELD_ARMED_24H


@pytest.mark.parametrize(
    "state",
    [
        S.S5_CRITICAL_INTERCEPTION,
        S.S6_COOLING_OFF_LOCK,
        S.S7_ESCALATED_SCAM_CONFIRMED,
        S.S9_GOLDEN_HOUR_TRIAGE,
    ],
)
def test_post_intercept_states_expire_with_shield(state: HsmState) -> None:
    f = LoanJourneyFiber("k", "s", state, T0, T0, shield_expires_at_ms=T0 + 24 * HOUR_MS)
    sup = FiberSupervisor([f])
    assert sup.expire(T0 + 24 * HOUR_MS - 1) == []
    sup.expire(T0 + 24 * HOUR_MS)
    assert sup.fibers == ()


def test_s9_without_shield_expires_24h_after_entering() -> None:
    sup = FiberSupervisor([LoanJourneyFiber("k", "s", S.S9_GOLDEN_HOUR_TRIAGE, T0, T0)])
    sup.expire(T0 + 24 * HOUR_MS - 1)
    assert len(sup.fibers) == 1
    sup.expire(T0 + 24 * HOUR_MS)
    assert list(sup.fibers) == []


def test_s2_without_progress_is_cleaned_after_4h() -> None:
    sup = make_sup()
    sup.apply_event("x", E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, now_ms=T0, event_id="e2")
    sup.expire(T0 + 4 * HOUR_MS - 1)
    assert len(sup.fibers) == 1
    sup.expire(T0 + 4 * HOUR_MS)
    assert list(sup.fibers) == []


def test_s3_after_4h_window_resumes_s4_keeps_evidence_then_bank_open_intercepts() -> None:
    """S3 hết cửa sổ 4h không bị kẹt: về S4 (giữ bằng chứng spoof, Khiên ≥ 24h từ lúc vào S3),
    mở app ngân hàng sau đó vẫn bị chặn (S5)."""
    sup = make_sup()
    sup.apply_event("x", E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, now_ms=T0, event_id="e2")
    sup.apply_event("x", E.V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT, now_ms=T0, event_id="v1")
    sup.expire(T0 + 5 * HOUR_MS)
    f = sup.get("x")
    assert f is not None and f.state is S.S4_SHIELD_ARMED_24H
    assert f.shield_expires_at_ms == T0 + 24 * HOUR_MS
    assert [e.event_id for e in f.evidence] == ["v1"]
    outs = sup.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + 6 * HOUR_MS)
    assert outs[0].accepted and outs[0].to_state is S.S5_CRITICAL_INTERCEPTION
    sup.expire(T0 + 24 * HOUR_MS)
    assert sup.fibers == ()


def test_k8_slots_freed_by_expiry_of_stuck_states() -> None:
    sup = make_sup()
    for i in range(K_MAX_FIBERS):
        sup.apply_event(f"a{i}", E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, now_ms=T0, event_id=f"{i}")
    assert len(sup.fibers) == K_MAX_FIBERS
    sup.expire(T0 + 5 * HOUR_MS)
    assert list(sup.fibers) == []


@pytest.mark.parametrize(
    "event",
    [E.E3_ANY_ORG_CREDIT_INFLOW, E.E4_HIGH_CONF_DISBURSEMENT_NOTIF, E.E5_B2B_WEBHOOK],
)
def test_credit_in_s1_arms_shield(event: HsmEvent) -> None:
    sup = make_sup()
    sup.apply_event("x", E.E1_APK_INSTALLED, now_ms=T0, event_id="apk")
    f = sup.arm_disbursement(
        "x", event, now_ms=T0 + HOUR_MS, event_id="d", source=EvidenceSource.EDGE_NOTIF
    )
    assert f is not None and f.state is S.S4_SHIELD_ARMED_24H
    assert f.shield_expires_at_ms == T0 + HOUR_MS + 24 * HOUR_MS


@pytest.mark.parametrize(
    "event",
    [E.E3_ANY_ORG_CREDIT_INFLOW, E.E4_HIGH_CONF_DISBURSEMENT_NOTIF, E.E5_B2B_WEBHOOK],
)
def test_credit_in_s2_arms_shield_and_bank_open_intercepts(event: HsmEvent) -> None:
    sup = make_sup()
    sup.apply_event("x", E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, now_ms=T0, event_id="e2")
    f = sup.arm_disbursement(
        "x", event, now_ms=T0 + 1000, event_id="d", source=EvidenceSource.EDGE_NOTIF
    )
    assert f is not None and f.state is S.S4_SHIELD_ARMED_24H
    assert f.shield_expires_at_ms == T0 + 1000 + 24 * HOUR_MS
    outs = sup.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=T0 + 2000)
    assert outs[0].accepted and outs[0].to_state is S.S5_CRITICAL_INTERCEPTION


def test_s1_credit_after_72h_starts_fresh_journey_not_half_state() -> None:
    sup = make_sup()
    sup.apply_event("x", E.E1_APK_INSTALLED, now_ms=T0, event_id="apk")
    f = sup.arm_disbursement(
        "x",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0 + 73 * HOUR_MS,
        event_id="d",
        source=EvidenceSource.EDGE_NOTIF,
    )
    assert f is not None and f.state is S.S4_SHIELD_ARMED_24H
    assert f.shield_expires_at_ms == T0 + 73 * HOUR_MS + 24 * HOUR_MS


def test_low_confidence_credit_without_journey_is_rejected_explicitly() -> None:
    sup = make_sup()
    f = sup.arm_disbursement(
        "x", E.E3_ANY_ORG_CREDIT_INFLOW, now_ms=T0, event_id="d", source=EvidenceSource.EDGE_NOTIF
    )
    assert f is None and sup.fibers == ()


def test_v2_prearm_72h_not_shortened_by_credit_assumption() -> None:
    """Giả định (lệch spec "Pre-arm 72h -> 24h on Credit" theo hướng an toàn): V2 pre-arm 72h,
    tiền về tại t0+1h → hạn Khiên vẫn là t0+72h (MAX), không rút về t0+1h+24h."""
    sup = make_sup()
    sup.apply_event("k", E.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, now_ms=T0, event_id="1")
    sup.apply_event("k", E.V2_LOAN_TERMS_EXTRACTED, now_ms=T0, event_id="2")
    f = sup.arm_disbursement(
        "k",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=T0 + HOUR_MS,
        event_id="3",
        source=EvidenceSource.EDGE_NOTIF,
    )
    assert f is not None and f.shield_expires_at_ms == T0 + 72 * HOUR_MS
