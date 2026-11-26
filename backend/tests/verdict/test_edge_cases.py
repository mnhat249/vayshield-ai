"""Edge case được giao cho T09: S2-EC13, S2-EC15, S2-EC16 (tích hợp), S2-EC17, S2-EC41, S2-EC42,
EC-AI24 + chỉ số sinh kế, thông điệp, chuyển đổi contract."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app.core.verdict_codes import KernelSignal, VerdictCode
from app.finance import CashFlowPoint, LoanTerms, RepaymentRegime, build_cashflows
from app.finance.solvers import BRACKET_HI, RateReport, analyze_cashflows
from app.verdict import (
    ALL_LEGAL_BASIS_IDS,
    LEGAL_BASIS_BY_VERDICT,
    AprInterval,
    Contradiction,
    DeductedFee,
    InMemoryLenderRegistry,
    MatchStatus,
    Provenance,
    RolloverTerms,
    SpecialMorphology,
    UpfrontFeeDemand,
    VerdictInput,
    VerdictInputError,
    deducted_fee_bounds,
    effective_apr,
    match_lender,
)
from tests.verdict._helpers import HOME_CREDIT, Decide, apr

V = VerdictCode


def _monthly(principal: int, installment: int, n: int, fee: int = 0) -> tuple[CashFlowPoint, ...]:
    return build_cashflows(
        LoanTerms(
            nominal_principal_vnd=principal,
            regimes=(RepaymentRegime(n, 30, installment),),
            deducted_at_source_vnd=fee,
        )
    )


# ------------------------------------------------------------------------- S2-EC13 ---


def test_s2_ec13_flat_rate_ad_contradiction_uses_real_cashflow(decide: Decide) -> None:
    """S2-EC13 / §1.3: "1,2%/tháng" nhưng trả 1.350.000đ × 12 cho 10tr → APR thật ~101,4%/năm."""
    flows = _monthly(10_000_000, 1_350_000, 12)
    report = analyze_cashflows(flows)
    # Đặc tả ghi "~96,5%/năm" là SAI: giải APR_VN-365 đúng (12 kỳ × 30 ngày, Actual/365) ra
    # ~101,4%/năm, tức cận dưới >= 100% → tín dụng đen.
    assert report.apr_primary is not None and 0.95 < report.apr_primary < 1.05
    inp = VerdictInput(
        claimed_lender_name="Vay Siêu Tốc", stated_annual_rate=0.144, cashflows=flows
    )
    v = decide(inp, AprInterval.from_report(report))
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY  # cận dưới >= 100%/năm
    assert Contradiction.STATED_RATE_CONTRADICTION in v.contradictions
    assert v.apr_vn_365 == report.apr_primary
    assert any("14,4%" in r for r in v.reasons_vi)


def test_s2_ec13_boundary_stated_rate_within_tolerance_no_contradiction(decide: Decide) -> None:
    inp = VerdictInput(claimed_lender_name="Home Credit", stated_annual_rate=0.300)
    v = decide(inp, apr(0.304))
    assert Contradiction.STATED_RATE_CONTRADICTION not in v.contradictions


# ------------------------------------------------------------------------- S2-EC15 ---


def test_s2_ec15_rollover_fee_7_days_is_criminal_usury(decide: Decide) -> None:
    """S2-EC15: phí gia hạn 1,8tr / 10tr / 7 ngày ≈ 938,6%/năm, không phải phí ứng trước."""
    inp = VerdictInput(
        claimed_lender_name="Vay Siêu Tốc",
        rollover=RolloverTerms(
            principal_vnd=10_000_000, extension_fee_vnd=1_800_000, extension_period_days=7
        ),
    )
    v = decide(inp, AprInterval.unavailable("NO_CASHFLOW"))
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY
    assert v.apr_min == pytest.approx(9.3857, abs=1e-3)
    assert Contradiction.LOAN_EXTENSION_FEE in v.contradictions
    assert Contradiction.ADVANCE_FEE_DEMANDED not in v.contradictions
    assert v.rollover is not None
    assert [p.horizon_days for p in v.rollover.projections] == [30, 60, 90]
    assert any("938,6%" in r for r in v.reasons_vi)


def test_s2_ec15_rollover_only_raises_existing_interval(decide: Decide) -> None:
    inp = VerdictInput(
        claimed_lender_name="Vay Siêu Tốc",
        rollover=RolloverTerms(
            principal_vnd=10_000_000, extension_fee_vnd=50_000, extension_period_days=30
        ),
    )
    v = decide(inp, apr(1.2, 1.5))  # phí gia hạn ~6%/năm không kéo APR xuống
    assert v.apr_min == 1.2 and v.apr_max == 1.5
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY


# ------------------------------------------------------------------------- S2-EC16 ---


def test_s2_ec16_entity_conflict_between_brand_and_legal_name(
    decide: Decide, registry: InMemoryLenderRegistry
) -> None:
    """Tên thương hiệu Home Credit nhưng tên pháp nhân của FinCo khác → mạo danh."""
    inp = VerdictInput(
        claimed_lender_name="Home Credit",
        claimed_company_legal_name="Công ty Tài chính TNHH MTV Shinhan Việt Nam",
    )
    m = match_lender(inp, Provenance(), registry)
    assert m.status is MatchStatus.LOOKALIKE and m.conflict
    v = decide(inp, apr(0.3))
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert Contradiction.BRAND_ENTITY_MISMATCH in v.contradictions


def test_s2_ec16_lookalike_domain_with_exact_name_is_spoof(decide: Decide) -> None:
    inp = VerdictInput(claimed_lender_name="Home Credit", claimed_domain="homecredit-vn.com")
    assert decide(inp, apr(0.3)).verdict_code is V.V_RED_BRAND_SPOOF


def test_s2_ec16_verified_apk_overrides_ocr_typo_in_legal_name(
    registry: InMemoryLenderRegistry,
) -> None:
    inp = VerdictInput(
        claimed_lender_name="Home Credit",
        claimed_company_legal_name="Công ty Tài chính TNHH MTV Home Credlt Việt Nam",
    )
    assert match_lender(inp, Provenance(), registry).status is MatchStatus.LOOKALIKE
    ok = Provenance(apk_signature_ok=True)
    assert match_lender(inp, ok, registry).status is MatchStatus.EXACT


def test_s2_ec16_package_name_matches_registry(registry: InMemoryLenderRegistry) -> None:
    m = match_lender(VerdictInput(), Provenance(package_name="vn.homecredit.demo"), registry)
    assert m.status is MatchStatus.EXACT and m.record is HOME_CREDIT


# ------------------------------------------------------------------------- S2-EC17 ---


def test_s2_ec17_p2p_sandbox_150m_exceeds_cap(decide: Decide) -> None:
    """S2-EC17: P2P Sandbox chào vay 150tr > trần 100tr → cảnh báo, không bao giờ XANH."""
    inp = VerdictInput(claimed_lender_name="Lendzy Demo", principal_vnd=150_000_000)
    v = decide(inp, apr(0.15))
    assert v.verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED
    assert Contradiction.P2P_SANDBOX_CAP_EXCEEDED in v.contradictions
    assert "ND_94_2025" in v.legal_basis_ids
    assert any("150.000.000đ" in r and "100.000.000đ" in r for r in v.reasons_vi)


def test_s2_ec17_p2p_over_cap_with_usury_rate_is_criminal(decide: Decide) -> None:
    inp = VerdictInput(claimed_lender_name="Lendzy Demo", principal_vnd=150_000_000)
    assert decide(inp, apr(1.2)).verdict_code is V.V_RED_CRIMINAL_USURY


def test_s2_ec17_boundary_exactly_100m_is_within_cap(decide: Decide) -> None:
    ok = VerdictInput(claimed_lender_name="Lendzy Demo", principal_vnd=100_000_000)
    over = VerdictInput(claimed_lender_name="Lendzy Demo", principal_vnd=100_000_001)
    verified = Provenance(apk_signature_ok=True)
    assert decide(ok, apr(0.15), verified).verdict_code is V.V_GREEN_TRANSPARENT
    assert decide(over, apr(0.15), verified).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


# ------------------------------------------------------------------ S2-EC41 / EC-AI24 ---


def test_ec_ai24_prechecked_insurance_in_worst_case_only() -> None:
    """EC-AI24: bảo hiểm tích sẵn vào kịch bản xấu nhất; best case giả định người vay bỏ chọn."""
    fees = (
        DeductedFee(label="Phí hồ sơ", amount_vnd=500_000),
        DeductedFee(label="[✓] Mua gói bảo hiểm sức khỏe", amount_vnd=1_200_000),
        DeductedFee(label="Phí SMS", amount_vnd=20_000, is_mandatory_or_prechecked=False),
    )
    bounds = deducted_fee_bounds(fees)
    assert bounds.best_case_vnd == 500_000
    assert bounds.worst_case_vnd == 1_700_000


def test_s2_ec41_glare_uncertainty_max_used_in_worst_case() -> None:
    fees = (DeductedFee(label="Phí bảo hiểm", amount_vnd=600_000, uncertainty_max_vnd=800_000),)
    assert deducted_fee_bounds(fees).worst_case_vnd == 800_000
    assert deducted_fee_bounds(fees).best_case_vnd == 0


def test_s2_ec41_interval_from_t02_reports_lower_bound_decides(decide: Decide) -> None:
    """S2-EC41: lóa dòng "Phí bảo hiểm 600.000đ" → giải 2 kịch bản bằng T02; cận dưới đã
    >= 100% → tín dụng đen bất biến."""
    best = analyze_cashflows(_monthly(5_000_000, 1_000_000, 8))
    worst = analyze_cashflows(_monthly(5_000_000, 1_000_000, 8, fee=600_000))
    interval = AprInterval.from_reports([best, worst])
    assert interval.apr_min is not None and interval.apr_max is not None
    assert interval.apr_min >= 1.0 and interval.apr_max > interval.apr_min
    v = decide(VerdictInput(claimed_lender_name="Vay Siêu Tốc"), interval)
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY
    assert v.apr_vn_365 is None  # không có điểm duy nhất khi là khoảng


def test_s2_ec41_interval_crossing_20_needs_confirmation(decide: Decide) -> None:
    best = analyze_cashflows(_monthly(10_000_000, 920_000, 12))
    worst = analyze_cashflows(_monthly(10_000_000, 920_000, 12, fee=600_000))
    interval = AprInterval.from_reports([best, worst])
    assert interval.apr_min is not None and interval.apr_max is not None
    assert interval.apr_min < 0.195 and interval.apr_max > 0.205
    v = decide(VerdictInput(claimed_lender_name="Vay Siêu Tốc"), interval)
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD in v.contradictions


def test_s2_ec41_one_unknown_scenario_makes_interval_unknown() -> None:
    good = analyze_cashflows(_monthly(10_000_000, 1_000_000, 12))
    bad = RateReport(None, None, None, 1, False, False, apr_unavailable_reason="RATE_BELOW_BRACKET")
    interval = AprInterval.from_reports([good, bad])
    assert not interval.known and interval.unavailable_reason == "RATE_BELOW_BRACKET"


# ------------------------------------------------------------------------- S2-EC42 ---


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (0.195, V.V_GREEN_TRANSPARENT),
        (0.2, V.V_GREEN_TRANSPARENT),
        (0.205, V.V_GREEN_TRANSPARENT),
        (0.2051, V.V_ORANGE_CIVIL_CAP_EXCEEDED),
    ],
)
def test_s2_ec42_half_point_tolerance_at_20(decide: Decide, rate: float, expected: V) -> None:
    """S2-EC42: dời ngày nghỉ làm APR nhích 20,x% — dung sai ±0,5 điểm % không báo oan."""
    inp = VerdictInput(claimed_lender_name="Lendzy Demo", principal_vnd=50_000_000)
    assert decide(inp, apr(rate), Provenance(apk_signature_ok=True)).verdict_code is expected


def test_s2_ec42_interval_inside_tolerance_band_is_not_crossing(decide: Decide) -> None:
    inp = VerdictInput(claimed_lender_name="Lendzy Demo", principal_vnd=50_000_000)
    v = decide(inp, apr(0.196, 0.204), Provenance(apk_signature_ok=True))
    assert v.verdict_code is V.V_GREEN_TRANSPARENT


# ----------------------------------------------------------------- AprInterval / RateReport ---


def test_effective_apr_conventions() -> None:
    above = RateReport(None, None, None, 1, False, True, apr_unavailable_reason="X")
    assert effective_apr(above) == BRACKET_HI
    multi = RateReport(None, None, 0.3, 3, True, False, apr_roots=(-0.5, -0.2))
    assert effective_apr(multi) == 0.3
    unknown = RateReport(None, None, None, 1, False, False)
    assert effective_apr(unknown) is None


def test_rate_above_bracket_is_criminal_for_unknown_lender(decide: Decide) -> None:
    above = RateReport(None, None, None, 1, False, True, apr_unavailable_reason="X")
    v = decide(VerdictInput(), AprInterval.from_report(above))
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY


def test_apr_interval_validation() -> None:
    with pytest.raises(ValueError, match="invalid"):
        AprInterval.from_bounds(0.5, 0.4)
    with pytest.raises(ValueError, match="both"):
        AprInterval(apr_min=0.1)


# ------------------------------------------------------------------- sinh kế + thông điệp ---


def test_livelihood_lost_workdays_from_cashflows(decide: Decide) -> None:
    flows = _monthly(10_000_000, 1_350_000, 12)
    v = decide(VerdictInput(claimed_lender_name="Vay Siêu Tốc", cashflows=flows), apr(0.965))
    assert v.c_loss_vnd == 6_200_000
    assert v.lost_workdays == Decimal("19.38")  # 6,2tr / 320.000đ
    assert "19,4 ngày công" in v.tts_script


def test_livelihood_custom_wage(decide: Decide) -> None:
    flows = _monthly(10_000_000, 1_350_000, 12)
    inp = VerdictInput(claimed_lender_name="Vay Siêu Tốc", cashflows=flows, daily_wage_vnd=200_000)
    assert decide(inp, apr(0.965)).lost_workdays == Decimal("31.00")


def test_advance_fee_lost_workdays_from_fee(decide: Decide) -> None:
    inp = VerdictInput(
        net_disbursed_vnd=0,
        upfront_fee_demand=UpfrontFeeDemand(demanded=True, amount_vnd=1_600_000),
    )
    v = decide(inp, AprInterval.advance_fee())
    assert v.lost_workdays == Decimal("5.00")
    assert v.kernel_signal is KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM


@pytest.mark.parametrize("code", list(VerdictCode))
def test_legal_basis_ids_are_known_node_codes(code: VerdictCode) -> None:
    ids = LEGAL_BASIS_BY_VERDICT[code]
    assert ids and set(ids) <= ALL_LEGAL_BASIS_IDS


_LAW_TEXT = re.compile(
    r"\b(?:[Đđ]iều|[Kk]hoản)\s+\d+|[Bb]ộ luật|[Nn]ghị định|[Tt]hông tư|BLHS|BLDS|NĐ\s*\d"
)


def _all_verdicts(decide: Decide) -> list[object]:
    hc = VerdictInput(claimed_lender_name="Home Credit")
    ok = Provenance(apk_signature_ok=True)
    return [
        decide(VerdictInput(upfront_fee_demand=UpfrontFeeDemand(demanded=True)), apr(0.1)),
        decide(VerdictInput(claimed_lender_name="Shinhan Vay Nhanh"), apr(0.3)),
        decide(VerdictInput(special_morphology=SpecialMorphology.BNPL_ILLEGAL_CASHOUT), apr(0.3)),
        decide(VerdictInput(), apr(1.5)),
        decide(VerdictInput(), apr(0.5)),
        decide(
            VerdictInput(claimed_lender_name="Cầm Đồ Kimlong Demo", is_pawn_asset_secured=True),
            apr(0.5),
        ),
        decide(hc, apr(0.5), ok),
        decide(hc, AprInterval.unavailable("X")),
        decide(hc, apr(0.2), ok),
    ]


def test_messages_cover_all_codes_and_contain_no_law_text_or_pii(decide: Decide) -> None:
    """tts/lý do tiếng Việt đời thường; căn cứ luật chỉ qua ``legal_basis_ids``; không lộ STK."""
    verdicts = _all_verdicts(decide)
    codes = {v.verdict_code for v in verdicts}  # type: ignore[attr-defined]
    assert codes == set(VerdictCode)
    for v in verdicts:
        tts: str = v.tts_script  # type: ignore[attr-defined]
        assert tts and "?" not in tts
        assert not _LAW_TEXT.search(tts)
        for reason in v.reasons_vi:  # type: ignore[attr-defined]
            assert not _LAW_TEXT.search(reason), reason
            assert "?" not in reason
            assert not re.search(r"\d{9,}", reason)


def test_to_audit_verdict_computed_converts_to_percent(decide: Decide) -> None:
    v = decide(VerdictInput(), apr(0.5, 0.6))
    out = v.to_audit_verdict_computed()
    assert out.verdict_code is v.verdict_code
    assert out.apr_min_bound == 50.0 and out.apr_max_bound == 60.0
    assert out.apr_vn_365 is None
    assert out.contradictions == [c.value for c in v.contradictions]


def test_to_audit_verdict_computed_hides_apr_for_advance_fee(decide: Decide) -> None:
    inp = VerdictInput(upfront_fee_demand=UpfrontFeeDemand(demanded=True, amount_vnd=1))
    out = decide(inp, apr(0.5)).to_audit_verdict_computed()
    assert out.apr_vn_365 is None and out.apr_min_bound is None and out.ear_xirr is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"principal_vnd": 0},
        {"principal_vnd": -5},
        {"net_disbursed_vnd": 1.5},
        {"daily_wage_vnd": 0},
        {"stated_annual_rate": -0.1},
        {"distinct_loan_brands_count": -1},
    ],
)
def test_input_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(VerdictInputError):
        VerdictInput(**kwargs)  # type: ignore[arg-type]


def test_rollover_terms_validation() -> None:
    with pytest.raises(VerdictInputError):
        RolloverTerms(principal_vnd=1, extension_fee_vnd=1, extension_period_days=0)


def test_advance_fee_hides_apr_from_reasons_and_rollover(decide: Decide) -> None:
    """Review minor: nhãn ẩn APR không lộ lãi suất qua lý do hay ``rollover.period_apr``."""
    inp = VerdictInput(
        stated_annual_rate=0.12,
        upfront_fee_demand=UpfrontFeeDemand(demanded=True, amount_vnd=500_000),
        rollover=RolloverTerms(
            principal_vnd=10_000_000, extension_fee_vnd=1_800_000, extension_period_days=7
        ),
    )
    v = decide(inp, apr(0.5, 1.2))
    assert v.verdict_code is V.V_RED_ADVANCE_FEE
    assert v.rollover is None
    assert not any("%" in r for r in v.reasons_vi)
    assert "%" not in v.tts_script


def test_legal_basis_has_no_invented_tctd_d100() -> None:
    assert "TCTD_2024_D100" not in ALL_LEGAL_BASIS_IDS
    assert LEGAL_BASIS_BY_VERDICT[V.V_YELLOW_HIGH_COST_LICENSED] == (
        "TCTD_2024_D15",
        "TT_43_2016_NHNN",
    )


@pytest.mark.parametrize(
    "name", ["Home Credit Việt Nam", "Công ty Tài chính Home Credit", "HOME CREDIT VN"]
)
def test_s2_ec16_real_name_variant_with_verified_apk_is_not_spoof(
    decide: Decide, registry: InMemoryLenderRegistry, name: str
) -> None:
    """Review BLOCKER: tên thật + từ chung chung, chữ ký APK đúng → EXACT, không RED_BRAND_SPOOF."""
    inp = VerdictInput(claimed_lender_name=name)
    ok = Provenance(apk_signature_ok=True)
    m = match_lender(inp, ok, registry)
    assert m.status is MatchStatus.EXACT and m.record is HOME_CREDIT
    v = decide(inp, apr(0.30), ok)
    assert v.verdict_code is V.V_GREEN_TRANSPARENT
    assert V.V_RED_BRAND_SPOOF not in v.fired_rules
    # Chỉ có ảnh chụp: không mạo danh nhưng tối đa AMBER (chưa xác thực kênh).
    assert decide(inp, apr(0.30)).verdict_code is V.V_AMBER_INCOMPLETE_DOC
