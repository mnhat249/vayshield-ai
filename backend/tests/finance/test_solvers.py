"""Bộ giải APR_VN-365 / EAR_XIRR / khoảng / MIRR (File 2 §4.2) — S2-EC12,13,15,40,41,42.

Ghi chú chênh lệch đặc tả (test theo CÔNG THỨC của File 2 §4.2, không ép số):
- XIRR ví dụ chuẩn 8,5tr→12tr/30 ngày: công thức ``(12/8,5)^(365/30) - 1`` = 6.539,085%,
  đặc tả ghi 6.568,5%.
- S2-EC12 (250k x 50 ngày / 10tr): công thức pháp lý dư nợ giảm dần = 333,20%, đặc tả ghi 351,8%.
- File 2 §1.3 (10tr / 12 tháng / 1,35tr): công thức = ~100,0% (tháng 365/12 ngày) hoặc 101,4%
  (tháng 30 ngày), đặc tả ghi ~96,5%.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from app.finance import (
    CashFlowPoint,
    FinanceError,
    LegalCapPosition,
    LoanTerms,
    MultipleRootsError,
    RateOutOfBracketError,
    RepaymentRegime,
    analyze_cashflows,
    build_cashflows,
    bullet_cashflows,
    compare_to_legal_cap,
    days_from_dates,
    interval_crosses,
    pmt,
    solve_apr_interval_bounds,
    solve_ear_xirr,
    solve_mirr,
    solve_vn_legal_apr_365,
    stated_rate_contradiction,
)
from app.finance.solvers import (
    npv_legal_365,
    npv_xirr,
    solve_ear_xirr_with_iterations,
    solve_vn_legal_apr_365_with_iterations,
)


def _bisect(f: Callable[[float], float], lo: float, hi: float) -> float:
    """Bộ giải tham chiếu độc lập (chia đôi thuần) để đối chiếu Brent."""
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _schedule(cf0: int, days: list[int], amount: int) -> tuple[CashFlowPoint, ...]:
    return (CashFlowPoint(0, cf0), *(CashFlowPoint(d, -amount) for d in days))


# ---------------------------------------------------------------- ví dụ chuẩn File 2 §4.2


def test_canonical_bullet_apr_501_pct() -> None:
    """Nhận 8,5tr trả 12tr sau 30 ngày → APR_VN-365 = 501,0%/năm (khớp đặc tả)."""
    apr = solve_vn_legal_apr_365(bullet_cashflows(8_500_000, 12_000_000, 30))
    assert apr == pytest.approx((12 - 8.5) / 8.5 * 365 / 30, abs=1e-9)
    assert round(apr * 100, 1) == 501.0


def test_canonical_bullet_xirr_matches_formula_not_spec_text() -> None:
    """XIRR = (12/8,5)^(365/30) - 1 = 6.539,085%. Đặc tả ghi 6.568,5% (lệch ~0,45%)."""
    xirr = solve_ear_xirr(bullet_cashflows(8_500_000, 12_000_000, 30))
    closed = (12 / 8.5) ** (365 / 30) - 1
    assert xirr == pytest.approx(closed, rel=1e-9)
    assert round(xirr * 100, 1) == 6539.1
    assert xirr > solve_vn_legal_apr_365(bullet_cashflows(8_500_000, 12_000_000, 30))


def test_brent_converges_under_45_iterations() -> None:
    cases = [
        bullet_cashflows(8_500_000, 12_000_000, 30),
        _schedule(10_000_000, list(range(1, 51)), 250_000),
        _schedule(10_000_000, [30 * k for k in range(1, 13)], 1_350_000),
    ]
    for flows in cases:
        _, it_apr = solve_vn_legal_apr_365_with_iterations(flows)
        _, it_xirr = solve_ear_xirr_with_iterations(flows)
        assert it_apr < 45
        assert it_xirr < 45


# ---------------------------------------------------------------- edge cases


def test_ec12_daily_installments_250k_x50_on_10m() -> None:
    """S2-EC12: góp 250k/ngày x 50 ngày cho 10tr, không ghi % → giải theo công thức.

    Công thức pháp lý (dư nợ giảm dần, Actual/365) cho 333,20%/năm; đặc tả ghi 351,8%.
    """
    terms = LoanTerms(nominal_principal_vnd=10_000_000, regimes=[RepaymentRegime(50, 1, 250_000)])
    flows = build_cashflows(terms)
    assert len(flows) == 51
    apr = solve_vn_legal_apr_365(flows)

    def annuity(i: float) -> float:  # khoản trả đều theo ngày, lãi ngày i
        return 10_000_000 - 250_000 * (1 - (1 + i) ** -50) / i

    reference = _bisect(annuity, 1e-9, 0.1) * 365
    assert apr == pytest.approx(reference, abs=1e-8)
    assert round(apr * 100, 1) == 333.2
    assert apr >= 1.0  # vẫn vượt mốc 100%/năm của Điều 201 BLHS


def test_ec13_flat_rate_vs_declining_balance() -> None:
    """S2-EC13: quảng cáo flat 1,5%/tháng (18%/năm) nhưng APR thực trên dòng tiền ~ gấp đôi."""
    principal, months = 12_000_000, 12
    flat_installment = principal // months + principal * 15 // 1000  # 1tr gốc + 180k lãi flat
    flows = _schedule(principal, [30 * k for k in range(1, months + 1)], flat_installment)
    apr = solve_vn_legal_apr_365(flows)
    assert 0.32 < apr < 0.36
    assert stated_rate_contradiction(0.18, apr)

    declining = pmt(principal, Decimal("0.015"), months)
    flows_ok = _schedule(principal, [30 * k for k in range(1, months + 1)], declining)
    apr_ok = solve_vn_legal_apr_365(flows_ok)
    assert apr_ok == pytest.approx(0.015 * 365 / 30, abs=1e-4)  # lãi 1,5%/kỳ 30 ngày
    assert not stated_rate_contradiction(0.18, apr_ok)


def test_ec13_section_1_3_stated_1_2pct_month_but_installment_1_35m() -> None:
    """File 2 §1.3: 'lãi 1,2%/tháng' 10tr/12 tháng nhưng trả 1,35tr/tháng.

    Theo lịch ngày thực 2025 (Actual/365) APR ~100%; đặc tả ghi ~96,5%.
    """
    dates = [date(2025 + (m // 12), m % 12 + 1, 15) for m in range(1, 13)]
    days = list(days_from_dates(date(2025, 1, 15), dates))
    flows = _schedule(10_000_000, days, 1_350_000)
    apr = solve_vn_legal_apr_365(flows)
    assert 0.99 < apr < 1.02
    assert stated_rate_contradiction(0.144, apr)
    # Kỳ đều 365/12 ngày cho đúng IRR tháng x 12 = 100,007%.
    xirr_month = _bisect(lambda r: 10e6 - sum(1.35e6 / (1 + r) ** k for k in range(1, 13)), 0, 1)
    assert apr == pytest.approx(xirr_month * 12, abs=0.01)


def test_ec15_rollover_fee_1_8m_on_10m_for_7_days() -> None:
    """S2-EC15: phí gia hạn 1,8tr/10tr/7 ngày → 938,6%/năm (khớp đặc tả)."""
    apr = solve_vn_legal_apr_365(bullet_cashflows(10_000_000, 11_800_000, 7))
    assert apr == pytest.approx(0.18 * 365 / 7, abs=1e-9)
    assert round(apr * 100, 1) == 938.6


def test_ec40_multiple_sign_changes_unique_root_plus_mirr() -> None:
    """S2-EC40: CF0>0, CF1<0, CF2>0 (hoàn phí), CF3<0 → vẫn 1 nghiệm kinh tế + có MIRR."""
    flows = (
        CashFlowPoint(0, 10_000_000),
        CashFlowPoint(30, -5_000_000),
        CashFlowPoint(45, 200_000),
        CashFlowPoint(60, -6_000_000),
    )
    report = analyze_cashflows(flows)
    assert report.sign_changes == 3
    assert not report.multiple_roots
    assert report.apr_vn_365 is not None
    assert npv_legal_365(flows, report.apr_vn_365) == pytest.approx(0.0, abs=1e-3)
    assert report.mirr is not None and report.mirr > 0


def test_ec40_true_multiple_irr_roots_detected() -> None:
    """S2-EC40: +1tr, -2,3tr (năm 1), +1,32tr (năm 2) → IRR 10% và 20%; khóa sang MIRR."""
    flows = (
        CashFlowPoint(0, 1_000_000),
        CashFlowPoint(365, -2_300_000),
        CashFlowPoint(730, 1_320_000),
    )
    with pytest.raises(MultipleRootsError) as exc:
        solve_ear_xirr(flows)
    assert sorted(exc.value.roots) == pytest.approx([0.10, 0.20], abs=1e-9)
    with pytest.raises(MultipleRootsError):
        solve_vn_legal_apr_365(flows)

    report = analyze_cashflows(flows)
    assert report.multiple_roots and report.apr_vn_365 is None and report.ear_xirr is None
    pv_out = 1_000_000 + 1_320_000 / 1.045**2
    fv_in = 2_300_000 * 1.045
    assert report.mirr == pytest.approx((fv_in / pv_out) ** 0.5 - 1, rel=1e-12)


def test_ec40_mirr_equals_xirr_for_bullet() -> None:
    flows = bullet_cashflows(8_500_000, 12_000_000, 30)
    assert solve_mirr(flows) == pytest.approx(solve_ear_xirr(flows), rel=1e-12)
    assert analyze_cashflows(flows).mirr is None  # chỉ bổ sung khi đổi dấu > 1 lần


def test_ec41_interval_bounds_insurance_glare() -> None:
    """S2-EC41: lóa dòng 'phí bảo hiểm 600k' — khấu trừ gốc hay cộng vào kỳ trả?"""
    days = [30 * k for k in range(1, 13)]
    deducted = _schedule(9_400_000, days, 1_000_000)
    added = _schedule(10_000_000, days, 1_050_000)
    lo, hi = solve_apr_interval_bounds(deducted, added)
    assert lo < hi
    assert (lo, hi) == solve_apr_interval_bounds(added, deducted)  # không phụ thuộc thứ tự
    assert lo == pytest.approx(min(solve_vn_legal_apr_365(deducted), solve_vn_legal_apr_365(added)))
    midpoint = (lo + hi) / 2
    assert interval_crosses((lo, hi), midpoint)
    assert not interval_crosses((lo, hi), hi + 0.01)


def test_ec41_usury_provable_when_lower_bound_above_100pct() -> None:
    """S2-EC41: kể cả kịch bản OCR có lợi nhất, APR_min >= 100% → bất biến toán học."""
    best = bullet_cashflows(9_000_000, 10_500_000, 30)
    worst = bullet_cashflows(8_400_000, 10_500_000, 30)
    lo, hi = solve_apr_interval_bounds(best, worst)
    assert lo >= 1.0
    assert not interval_crosses((lo, hi), 1.0)


def test_ec42_actual_365_tolerance_at_20pct_boundary() -> None:
    """S2-EC42: dung sai ±0,5 điểm % quanh mốc 20%."""
    assert compare_to_legal_cap(0.2049) is LegalCapPosition.AT_BOUNDARY
    assert compare_to_legal_cap(0.1951) is LegalCapPosition.AT_BOUNDARY
    assert compare_to_legal_cap(0.2051) is LegalCapPosition.ABOVE
    assert compare_to_legal_cap(0.1949) is LegalCapPosition.BELOW


def test_ec42_weekend_shift_does_not_flag_20pct_loan() -> None:
    """S2-EC42: khoản 20%/năm lịch 30/360, ngày trả rơi Chủ nhật dời sang thứ Hai (Actual/365).

    APR thực theo ngày dương lịch lệch nhẹ khỏi 20% nhưng nằm trong dung sai → không báo vượt.
    """
    principal = 10_000_000
    installment = pmt(principal, Decimal("0.20") / 12, 12)
    due = []
    for m in range(1, 13):
        d = date(2025 + (m // 12), m % 12 + 1, 1)
        if d.weekday() == 6:  # Chủ nhật → thứ Hai
            d = d.replace(day=2)
        due.append(d)
    days = list(days_from_dates(date(2025, 1, 1), due))
    apr = solve_vn_legal_apr_365(_schedule(principal, days, installment))
    assert apr != pytest.approx(0.20, abs=1e-6)  # có lệch do quy ước đếm ngày
    assert compare_to_legal_cap(apr) is LegalCapPosition.AT_BOUNDARY


# ---------------------------------------------------------------- lỗi / suy biến


def test_invalid_cashflows_rejected() -> None:
    with pytest.raises(FinanceError) as exc:
        solve_vn_legal_apr_365([CashFlowPoint(0, -1_000), CashFlowPoint(30, 2_000)])
    assert exc.value.code == "INVALID_OR_ADVANCE_FEE_CASHFLOW"
    with pytest.raises(FinanceError) as exc:
        solve_vn_legal_apr_365([CashFlowPoint(5, 1_000), CashFlowPoint(30, -2_000)])
    assert exc.value.code == "INVALID_OR_ADVANCE_FEE_CASHFLOW"
    with pytest.raises(FinanceError) as exc:
        solve_ear_xirr([CashFlowPoint(0, 1_000), CashFlowPoint(30, 2_000)])
    assert exc.value.code == "MISSING_REPAYMENT_OUTFLOWS"
    with pytest.raises(FinanceError):
        solve_vn_legal_apr_365([CashFlowPoint(0, 1_000), CashFlowPoint(0, -2_000)])
    with pytest.raises(FinanceError):
        solve_vn_legal_apr_365([])


def test_rate_above_bracket_reported_not_crash() -> None:
    """XIRR của 1 ngày gấp đôi = 2^365 - 1 → vượt kẹp 500, báo 'above' thay vì tràn số."""
    flows = bullet_cashflows(1_000_000, 2_000_000, 1)
    with pytest.raises(RateOutOfBracketError) as exc:
        solve_ear_xirr(flows)
    assert exc.value.direction == "above"
    assert solve_vn_legal_apr_365(flows) == pytest.approx(365.0, rel=1e-9)
    report = analyze_cashflows(flows)
    assert report.ear_xirr is None and report.apr_vn_365 == pytest.approx(365.0)


def test_negative_rate_when_repay_less_than_received() -> None:
    flows = bullet_cashflows(10_000_000, 9_000_000, 365)
    assert solve_vn_legal_apr_365(flows) == pytest.approx(-0.1, abs=1e-9)
    assert solve_ear_xirr(flows) == pytest.approx(-0.1, abs=1e-9)


def test_long_tenure_installments_36_months() -> None:
    """Trả góp 36 tháng (File 6 QA hàng 1) — APR trùng lãi kỳ x 365/30."""
    installment = pmt(30_000_000, Decimal("0.02"), 36)
    flows = _schedule(30_000_000, [30 * k for k in range(1, 37)], installment)
    assert solve_vn_legal_apr_365(flows) == pytest.approx(0.02 * 365 / 30, abs=1e-5)
    xirr = solve_ear_xirr(flows)
    assert npv_xirr(flows, xirr) == pytest.approx(0.0, abs=1e-3)
    assert xirr == pytest.approx(1.02 ** (365 / 30) - 1, abs=1e-5)


def test_legal_solver_handles_gaps_longer_than_one_year() -> None:
    flows = bullet_cashflows(10_000_000, 14_000_000, 730)
    assert solve_vn_legal_apr_365(flows) == pytest.approx(0.2, abs=1e-9)
    assert solve_ear_xirr(flows) == pytest.approx(math.sqrt(1.4) - 1, abs=1e-9)
