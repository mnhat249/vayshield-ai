"""Bộ sinh vector CF (File 2 §4.1, §4.2 mở rộng) — S2-EC14, S2-EC39, bất biến Bẫy 1."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.verdict_codes import KernelSignal
from app.finance import (
    AdvanceFeeScamError,
    CashFlowPoint,
    CommodityResale,
    FinanceError,
    LoanTerms,
    RepaymentRegime,
    build_cashflows,
    bullet_cashflows,
    count_sign_changes,
    days_from_dates,
    pmt,
    solve_vn_legal_apr_365,
)
from app.finance.solvers import npv_legal_365


def test_cf0_formula_nominal_minus_deducted_bundled_upfront() -> None:
    terms = LoanTerms(
        nominal_principal_vnd=10_000_000,
        deducted_at_source_vnd=1_000_000,
        bundled_mandatory_vnd=500_000,
        upfront_out_of_pocket_vnd=200_000,
        disbursement_received_before_fee=True,
        regimes=[RepaymentRegime(1, 30, 12_000_000)],
    )
    flows = build_cashflows(terms)
    assert flows[0] == CashFlowPoint(0, 10_000_000 - 1_000_000 - 500_000 - 200_000)
    assert flows[1] == CashFlowPoint(30, -12_000_000)


def test_advance_fee_invariant_stops_solver_with_kernel_signal() -> None:
    """Bất biến Bẫy 1: đòi phí trước giải ngân → INFINITE_APR_ADVANCE_FEE_SCAM."""
    terms = LoanTerms(
        nominal_principal_vnd=20_000_000,
        upfront_out_of_pocket_vnd=1_500_000,
        regimes=[RepaymentRegime(12, 30, 2_000_000)],
    )
    with pytest.raises(AdvanceFeeScamError) as exc:
        build_cashflows(terms)
    assert exc.value.code == KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM.value
    assert exc.value.signal is KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM


def test_advance_fee_with_zero_disbursement_is_scam_even_if_flag_set() -> None:
    terms = LoanTerms(
        nominal_principal_vnd=0,
        upfront_out_of_pocket_vnd=300_000,
        disbursement_received_before_fee=True,
        regimes=[RepaymentRegime(1, 30, 1_000_000)],
    )
    with pytest.raises(AdvanceFeeScamError):
        build_cashflows(terms)


def test_fees_consuming_whole_principal_is_invalid() -> None:
    terms = LoanTerms(
        nominal_principal_vnd=5_000_000,
        deducted_at_source_vnd=5_000_000,
        regimes=[RepaymentRegime(1, 30, 6_000_000)],
    )
    with pytest.raises(FinanceError) as exc:
        build_cashflows(terms)
    assert exc.value.code == "INVALID_OR_ADVANCE_FEE_CASHFLOW"


def test_missing_repayments_rejected() -> None:
    with pytest.raises(FinanceError) as exc:
        build_cashflows(LoanTerms(nominal_principal_vnd=5_000_000, regimes=[]))
    assert exc.value.code == "MISSING_REPAYMENT_OUTFLOWS"


def test_ec14_teaser_zero_rate_with_25pct_deducted_fee() -> None:
    """S2-EC14: 'lãi 0%' nhưng khấu trừ 25% phí tư vấn tại nguồn → CF0 = 7,5tr, APR > 400%."""
    terms = LoanTerms(
        nominal_principal_vnd=10_000_000,
        deducted_at_source_vnd=2_500_000,
        regimes=[RepaymentRegime(1, 30, 10_000_000)],
    )
    flows = build_cashflows(terms)
    assert flows[0].amount_vnd == 7_500_000
    apr = solve_vn_legal_apr_365(flows)
    assert apr == pytest.approx(2.5 / 7.5 * 365 / 30, rel=1e-9)  # 405,56%/năm, không phải 0%


def test_ec14_teaser_installments_with_25pct_deducted_fee_positive_apr() -> None:
    """S2-EC14 trả góp: 4 kỳ x 2,5tr gốc 0% lãi, khấu 25% → APR thực dương và lớn."""
    terms = LoanTerms(
        nominal_principal_vnd=10_000_000,
        deducted_at_source_vnd=2_500_000,
        regimes=[RepaymentRegime(4, 30, 2_500_000)],
    )
    flows = build_cashflows(terms)
    apr = solve_vn_legal_apr_365(flows)
    assert apr > 1.0
    assert npv_legal_365(flows, apr) == pytest.approx(0.0, abs=1e-3)


def test_ec39_two_stage_grace_then_pmt_schedule() -> None:
    """S2-EC39: ân hạn gốc 2 kỳ (chỉ phí 150k) rồi 10 kỳ PMT(r2) — tách đúng từng kỳ."""
    installment = pmt(10_000_000, Decimal("0.04"), 10)
    terms = LoanTerms(
        nominal_principal_vnd=10_000_000,
        regimes=[RepaymentRegime(2, 30, 150_000), RepaymentRegime(10, 30, installment)],
    )
    flows = build_cashflows(terms)
    assert len(flows) == 13
    assert [p.day for p in flows] == [30 * k for k in range(13)]
    assert flows[1].amount_vnd == flows[2].amount_vnd == -150_000
    assert all(p.amount_vnd == -installment for p in flows[3:])


def test_ec39_three_small_months_then_balloon() -> None:
    """S2-EC39: '3 tháng đầu chỉ đóng 200k, tháng thứ 4 đóng dồn gốc + lãi phạt'."""
    terms = LoanTerms(
        nominal_principal_vnd=5_000_000,
        regimes=[RepaymentRegime(3, 30, 200_000), RepaymentRegime(1, 30, 6_200_000)],
    )
    flows = build_cashflows(terms)
    assert [p.amount_vnd for p in flows] == [5_000_000, -200_000, -200_000, -200_000, -6_200_000]
    apr = solve_vn_legal_apr_365(flows)
    assert npv_legal_365(flows, apr) == pytest.approx(0.0, abs=1e-3)
    # Khác hẳn giả định PMT đều 4 kỳ cùng tổng 6,8tr (dồn cuối làm APR thấp hơn kỳ đều).
    even = build_cashflows(
        LoanTerms(nominal_principal_vnd=5_000_000, regimes=[RepaymentRegime(4, 30, 1_700_000)])
    )
    assert abs(solve_vn_legal_apr_365(even) - apr) > 0.1


def test_commodity_resale_cf0_uses_buyback_minus_broker() -> None:
    """Tín dụng đen ngụy trang mua bán hàng hóa: CF0 = 20tr - 1tr, nghĩa vụ trên 30tr."""
    terms = LoanTerms(
        nominal_principal_vnd=30_000_000,
        commodity_resale=CommodityResale(30_000_000, 20_000_000, 1_000_000),
        regimes=[RepaymentRegime(12, 30, 2_900_000)],
    )
    flows = build_cashflows(terms)
    assert flows[0].amount_vnd == 19_000_000
    assert solve_vn_legal_apr_365(flows) > 1.0


def test_s2_ec41_commodity_resale_with_600k_insurance_deducted() -> None:
    """S2-EC41 + hàng bán lại: bảo hiểm 600k khấu trừ / 400k bán kèm vẫn bị trừ khỏi CF0."""
    regimes = [RepaymentRegime(12, 30, 2_900_000)]
    plain = build_cashflows(
        LoanTerms(
            nominal_principal_vnd=30_000_000,
            regimes=regimes,
            commodity_resale=CommodityResale(30_000_000, 20_000_000, 1_000_000),
        )
    )
    with_fees = build_cashflows(
        LoanTerms(
            nominal_principal_vnd=30_000_000,
            regimes=regimes,
            commodity_resale=CommodityResale(30_000_000, 20_000_000, 1_000_000),
            deducted_at_source_vnd=600_000,
            bundled_mandatory_vnd=400_000,
        )
    )
    assert with_fees[0].amount_vnd == 20_000_000 - 1_000_000 - 600_000 - 400_000
    assert with_fees[1:] == plain[1:]
    assert solve_vn_legal_apr_365(with_fees) > solve_vn_legal_apr_365(plain)


def test_commodity_resale_buyback_above_invoice_rejected() -> None:
    """Bất biến: tiền mua lại không thể vượt giá hóa đơn → INVALID_INPUT."""
    terms = LoanTerms(
        nominal_principal_vnd=30_000_000,
        commodity_resale=CommodityResale(30_000_000, 30_000_001, 0),
        regimes=[RepaymentRegime(12, 30, 2_900_000)],
    )
    with pytest.raises(FinanceError) as exc:
        build_cashflows(terms)
    assert exc.value.code == "INVALID_INPUT"


def test_extra_flows_merge_same_day_and_sorted() -> None:
    terms = LoanTerms(
        nominal_principal_vnd=10_000_000,
        regimes=[RepaymentRegime(2, 30, 5_500_000)],
        extra_flows=[CashFlowPoint(60, -100_000), CashFlowPoint(45, 50_000)],
    )
    flows = build_cashflows(terms)
    assert [(p.day, p.amount_vnd) for p in flows] == [
        (0, 10_000_000),
        (30, -5_500_000),
        (45, 50_000),
        (60, -5_600_000),
    ]


def test_cashflow_point_rejects_float_and_negative_day() -> None:
    with pytest.raises(FinanceError):
        CashFlowPoint(-1, 1000)
    with pytest.raises(FinanceError):
        CashFlowPoint(1, 1000.5)  # type: ignore[arg-type]


def test_bullet_helper_and_sign_changes() -> None:
    flows = bullet_cashflows(8_500_000, 12_000_000, 30)
    assert count_sign_changes(flows) == 1
    with_cashback = (*flows[:1], CashFlowPoint(10, -1), CashFlowPoint(20, 5), flows[1])
    assert count_sign_changes(with_cashback) == 3


def test_ec42_days_from_dates_is_actual_calendar() -> None:
    """S2-EC42: đếm ngày thực (Actual/365), không quy về tháng 30 ngày."""
    days = days_from_dates(date(2025, 1, 31), [date(2025, 2, 28), date(2025, 3, 31)])
    assert days == (28, 59)
    with pytest.raises(FinanceError):
        days_from_dates(date(2025, 1, 31), [date(2025, 1, 31)])


def test_pmt_known_value() -> None:
    assert pmt(10_000_000, Decimal("0.01"), 12) == 888_488
    assert pmt(1_200_000, Decimal(0), 12) == 100_000
