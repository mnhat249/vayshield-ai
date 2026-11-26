"""Chỉ số áp lực sinh kế & xoáy nợ 30-60-90 ngày (File 2 §4.3) — S2-EC15."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.finance import (
    CashFlowPoint,
    FinanceError,
    RolloverMode,
    bullet_cashflows,
    daily_wage_from_monthly_income,
    livelihood_pain,
    rollover_spiral,
)


def test_c_loss_and_lost_workdays_default_wage_320k() -> None:
    pain = livelihood_pain(bullet_cashflows(8_500_000, 12_000_000, 30))
    assert pain.c_loss_vnd == 3_500_000
    assert pain.daily_wage_vnd == 320_000
    assert pain.lost_workdays == Decimal("10.94")


def test_ec12_daily_installment_c_loss() -> None:
    """S2-EC12: tổng trả 12,5tr cho 10tr → mất 2,5tr = 7,81 ngày công."""
    flows = (CashFlowPoint(0, 10_000_000), *(CashFlowPoint(d, -250_000) for d in range(1, 51)))
    pain = livelihood_pain(flows)
    assert pain.c_loss_vnd == 2_500_000
    assert pain.lost_workdays == Decimal("7.81")


def test_wage_slider_from_monthly_income() -> None:
    wage = daily_wage_from_monthly_income(7_800_000, 26)
    assert wage == 300_000
    pain = livelihood_pain(bullet_cashflows(8_500_000, 12_000_000, 30), daily_wage_vnd=wage)
    assert pain.lost_workdays == Decimal("11.67")


def test_livelihood_rejects_invalid_wage() -> None:
    with pytest.raises(FinanceError):
        livelihood_pain(bullet_cashflows(1_000_000, 1_100_000, 30), daily_wage_vnd=0)
    with pytest.raises(FinanceError):
        daily_wage_from_monthly_income(0, 26)


def test_ec15_rollover_spiral_30_60_90_days() -> None:
    """S2-EC15: gia hạn 1,8tr/10tr mỗi 7 ngày → 938,6%/năm và nợ phình theo 30-60-90 ngày."""
    spiral = rollover_spiral(10_000_000, 1_800_000, 7)
    assert round(spiral.period_apr * 100, 1) == 938.6
    got = [(p.horizon_days, p.extensions, p.cumulative_fees_vnd) for p in spiral.projections]
    assert got == [(30, 5, 9_000_000), (60, 9, 16_200_000), (90, 13, 23_400_000)]
    assert all(p.outstanding_principal_vnd == 10_000_000 for p in spiral.projections)
    assert spiral.projections[-1].total_burden_vnd == 33_400_000


def test_rollover_capitalize_mode_compounds() -> None:
    spiral = rollover_spiral(10_000_000, 1_000_000, 30, mode=RolloverMode.CAPITALIZE)
    debts = [p.outstanding_principal_vnd for p in spiral.projections]
    assert debts == [11_000_000, 12_100_000, 13_310_000]
    assert spiral.projections[-1].cumulative_fees_vnd == 3_310_000


def test_rollover_rejects_invalid_terms() -> None:
    with pytest.raises(FinanceError):
        rollover_spiral(0, 1_000, 7)
    with pytest.raises(FinanceError):
        rollover_spiral(1_000_000, 1_000, 0)
    with pytest.raises(FinanceError):
        rollover_spiral(1_000_000, 1_000, 7, horizons_days=[0])
