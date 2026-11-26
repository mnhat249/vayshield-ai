"""Chỉ số áp lực sinh kế & mô phỏng xoáy nợ gia hạn (File 2 §4.3).

Tiền tính bằng ``Decimal``/int đồng; số ngày công trả về ``Decimal`` làm tròn 2 chữ số.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from app.finance.cashflow import CashFlowPoint
from app.finance.errors import INVALID_INPUT, FinanceError

__all__ = [
    "DEFAULT_DAILY_WAGE_VND",
    "DEFAULT_ROLLOVER_HORIZONS_DAYS",
    "LivelihoodPain",
    "RolloverMode",
    "RolloverProjection",
    "RolloverSpiral",
    "daily_wage_from_monthly_income",
    "livelihood_pain",
    "rollover_spiral",
]

DEFAULT_DAILY_WAGE_VND: Final = 320_000
DEFAULT_ROLLOVER_HORIZONS_DAYS: Final = (30, 60, 90)
_CENT: Final = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class LivelihoodPain:
    c_loss_vnd: int
    daily_wage_vnd: int
    lost_workdays: Decimal


def livelihood_pain(
    flows: Sequence[CashFlowPoint], daily_wage_vnd: int = DEFAULT_DAILY_WAGE_VND
) -> LivelihoodPain:
    """``C_loss = sum|CF_k| - CF_0`` và ``D_work = C_loss / W_daily`` (mặc định 320.000đ)."""
    if daily_wage_vnd <= 0:
        raise FinanceError(INVALID_INPUT, "daily_wage_vnd must be > 0")
    ordered = sorted(flows, key=lambda p: p.day)
    if not ordered or ordered[0].day != 0 or ordered[0].amount_vnd <= 0:
        raise FinanceError(INVALID_INPUT, "first flow must be net received at day 0")
    paid = sum(abs(p.amount_vnd) for p in ordered[1:] if p.amount_vnd < 0)
    refunds = sum(p.amount_vnd for p in ordered[1:] if p.amount_vnd > 0)
    c_loss = paid - refunds - ordered[0].amount_vnd
    days = (Decimal(c_loss) / Decimal(daily_wage_vnd)).quantize(_CENT, rounding=ROUND_HALF_UP)
    return LivelihoodPain(c_loss_vnd=c_loss, daily_wage_vnd=daily_wage_vnd, lost_workdays=days)


def daily_wage_from_monthly_income(monthly_income_vnd: int, workdays_per_month: int) -> int:
    """Thanh trượt thu nhập tháng → ngày công. Số ngày công/tháng do người gọi chỉ định."""
    if monthly_income_vnd <= 0 or workdays_per_month <= 0:
        raise FinanceError(INVALID_INPUT, "income and workdays must be > 0")
    value = Decimal(monthly_income_vnd) / Decimal(workdays_per_month)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


class RolloverMode(StrEnum):
    PAY_FEE = "PAY_FEE"  # Đóng phí gia hạn bằng tiền túi, gốc giữ nguyên
    CAPITALIZE = "CAPITALIZE"  # Không đóng nổi phí → phí nhập gốc, kỳ sau tính trên nợ mới


@dataclass(frozen=True, slots=True)
class RolloverProjection:
    horizon_days: int
    extensions: int
    cumulative_fees_vnd: int
    outstanding_principal_vnd: int
    total_burden_vnd: int


@dataclass(frozen=True, slots=True)
class RolloverSpiral:
    period_apr: float
    mode: RolloverMode
    projections: tuple[RolloverProjection, ...]


def rollover_spiral(
    principal_vnd: int,
    extension_fee_vnd: int,
    extension_period_days: int,
    horizons_days: Sequence[int] = DEFAULT_ROLLOVER_HORIZONS_DAYS,
    mode: RolloverMode = RolloverMode.PAY_FEE,
) -> RolloverSpiral:
    """Dự phóng nợ sau 30–60–90 ngày nếu không trả nổi gốc và phải gia hạn liên tục.

    Giả định: gia hạn được mua ở đầu mỗi chu kỳ (ngày 0, P, 2P, …) nên để không vỡ nợ trong
    ``H`` ngày cần ``ceil(H / P)`` lần gia hạn. ``period_apr = fee/principal · 365/P``
    (S2-EC15: 1,8tr/10tr/7 ngày → 938,57%/năm).
    """
    if principal_vnd <= 0 or extension_fee_vnd < 0 or extension_period_days <= 0:
        raise FinanceError(INVALID_INPUT, "invalid rollover terms")
    if any(h <= 0 for h in horizons_days):
        raise FinanceError(INVALID_INPUT, "horizons must be > 0")
    fee_rate = Decimal(extension_fee_vnd) / Decimal(principal_vnd)
    period_apr = float(fee_rate * Decimal(365) / Decimal(extension_period_days))
    projections: list[RolloverProjection] = []
    for h in horizons_days:
        n = math.ceil(h / extension_period_days)
        if mode is RolloverMode.PAY_FEE:
            fees = n * extension_fee_vnd
            outstanding = principal_vnd
            burden = principal_vnd + fees
        else:
            debt = Decimal(principal_vnd) * (Decimal(1) + fee_rate) ** n
            outstanding = int(debt.quantize(Decimal(1), rounding=ROUND_HALF_UP))
            fees = outstanding - principal_vnd
            burden = outstanding
        projections.append(
            RolloverProjection(
                horizon_days=h,
                extensions=n,
                cumulative_fees_vnd=fees,
                outstanding_principal_vnd=outstanding,
                total_burden_vnd=burden,
            )
        )
    return RolloverSpiral(period_apr=period_apr, mode=mode, projections=tuple(projections))
