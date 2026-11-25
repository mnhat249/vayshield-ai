"""Vector dòng tiền tổng quát CF và bộ sinh đa chế độ (File 2 §4.1, §4.2 mở rộng).

Quy ước: ``day`` là số ngày thực (Actual/365) kể từ giải ngân ``t_0 = 0``; ``amount_vnd`` là
int đồng, dương = tiền người vay nhận ròng, âm = tiền người vay trả ra.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.finance.errors import (
    INVALID_INPUT,
    INVALID_OR_ADVANCE_FEE_CASHFLOW,
    MISSING_REPAYMENT_OUTFLOWS,
    AdvanceFeeScamError,
    FinanceError,
)

__all__ = [
    "CashFlowPoint",
    "CommodityResale",
    "LoanTerms",
    "RepaymentRegime",
    "build_cashflows",
    "bullet_cashflows",
    "count_sign_changes",
    "days_from_dates",
    "pmt",
]


@dataclass(frozen=True, slots=True)
class CashFlowPoint:
    """Một điểm dòng tiền ``(t_k, CF_k)``."""

    day: int
    amount_vnd: int

    def __post_init__(self) -> None:
        if isinstance(self.day, bool) or not isinstance(self.day, int) or self.day < 0:
            raise FinanceError(INVALID_INPUT, "day must be int >= 0")
        if isinstance(self.amount_vnd, bool) or not isinstance(self.amount_vnd, int):
            raise FinanceError(INVALID_INPUT, "amount_vnd must be int VND")


@dataclass(frozen=True, slots=True)
class RepaymentRegime:
    """Một chế độ trả nợ: ``period_count`` kỳ, cách nhau ``period_interval_days``, mỗi kỳ trả
    ``amount_per_period_vnd`` (số dương). Các chế độ nối tiếp nhau theo thứ tự (S2-EC39)."""

    period_count: int
    period_interval_days: int
    amount_per_period_vnd: int


@dataclass(frozen=True, slots=True)
class CommodityResale:
    """Tín dụng đen ngụy trang mua hàng – bán lại tại quầy (File 2 §4.2 mở rộng mục 2).

    Người vay thực nhận ``buyback_cash_vnd - broker_fee_vnd``; nghĩa vụ trả nợ vẫn tính trên
    giá hóa đơn (do ``regimes`` mô tả).
    """

    invoice_price_vnd: int
    buyback_cash_vnd: int
    broker_fee_vnd: int = 0


@dataclass(frozen=True, slots=True)
class LoanTerms:
    """Đầu vào chuẩn hóa cho ``build_cashflows`` (từ trích xuất LLM hoặc form người dùng).

    ``disbursement_received_before_fee`` mặc định ``False`` (an toàn nhất): mọi khoản
    ``upfront_out_of_pocket_vnd > 0`` bị coi là đòi trước khi giải ngân → Bẫy 1.
    ``extra_flows`` cho phép thêm điểm lẻ (khoản dồn cuối kỳ, hoàn phí giữa kỳ dương...).
    """

    nominal_principal_vnd: int
    regimes: Sequence[RepaymentRegime]
    deducted_at_source_vnd: int = 0
    bundled_mandatory_vnd: int = 0
    upfront_out_of_pocket_vnd: int = 0
    disbursement_received_before_fee: bool = False
    commodity_resale: CommodityResale | None = None
    extra_flows: Sequence[CashFlowPoint] = field(default_factory=tuple)


def _require_non_negative(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FinanceError(INVALID_INPUT, f"{name} must be int >= 0")


def _merge(points: Iterable[CashFlowPoint]) -> tuple[CashFlowPoint, ...]:
    by_day: dict[int, int] = {}
    for p in points:
        by_day[p.day] = by_day.get(p.day, 0) + p.amount_vnd
    return tuple(
        CashFlowPoint(day=d, amount_vnd=a) for d, a in sorted(by_day.items()) if a != 0 or d == 0
    )


def build_cashflows(terms: LoanTerms) -> tuple[CashFlowPoint, ...]:
    """Sinh vector CF đã sắp xếp, ngày tăng dần, gộp các điểm cùng ngày.

    ``CF_0 = P_nominal - F_deducted - I_bundled - F_upfront`` (hoặc ``buyback - broker - upfront``
    với hàng hóa bán lại). Raise ``AdvanceFeeScamError`` khi đòi phí trước giải ngân.
    """
    _require_non_negative("nominal_principal_vnd", terms.nominal_principal_vnd)
    _require_non_negative("deducted_at_source_vnd", terms.deducted_at_source_vnd)
    _require_non_negative("bundled_mandatory_vnd", terms.bundled_mandatory_vnd)
    _require_non_negative("upfront_out_of_pocket_vnd", terms.upfront_out_of_pocket_vnd)

    if terms.commodity_resale is not None:
        cr = terms.commodity_resale
        _require_non_negative("buyback_cash_vnd", cr.buyback_cash_vnd)
        _require_non_negative("broker_fee_vnd", cr.broker_fee_vnd)
        disbursed = cr.buyback_cash_vnd - cr.broker_fee_vnd
    else:
        disbursed = (
            terms.nominal_principal_vnd - terms.deducted_at_source_vnd - terms.bundled_mandatory_vnd
        )

    upfront = terms.upfront_out_of_pocket_vnd
    if upfront > 0 and (disbursed <= 0 or not terms.disbursement_received_before_fee):
        raise AdvanceFeeScamError()

    cf0 = disbursed - upfront
    if cf0 <= 0:
        raise FinanceError(INVALID_OR_ADVANCE_FEE_CASHFLOW, "net received must be > 0")

    points: list[CashFlowPoint] = [CashFlowPoint(day=0, amount_vnd=cf0)]
    offset = 0
    for regime in terms.regimes:
        if regime.period_count < 1 or regime.period_interval_days < 1:
            raise FinanceError(INVALID_INPUT, "regime needs period_count/interval >= 1")
        _require_non_negative("amount_per_period_vnd", regime.amount_per_period_vnd)
        for k in range(1, regime.period_count + 1):
            points.append(
                CashFlowPoint(
                    day=offset + k * regime.period_interval_days,
                    amount_vnd=-regime.amount_per_period_vnd,
                )
            )
        offset += regime.period_count * regime.period_interval_days
    points.extend(terms.extra_flows)

    flows = _merge(points)
    if flows[0].day != 0 or flows[0].amount_vnd <= 0:
        raise FinanceError(INVALID_OR_ADVANCE_FEE_CASHFLOW, "net received must be > 0")
    if not any(p.amount_vnd < 0 for p in flows[1:]):
        raise FinanceError(MISSING_REPAYMENT_OUTFLOWS)
    return flows


def bullet_cashflows(
    net_received_vnd: int, total_repayment_vnd: int, tenure_days: int
) -> tuple[CashFlowPoint, ...]:
    """Khoản vay trả 1 lần cuối kỳ (trường hợp A)."""
    if net_received_vnd <= 0:
        raise FinanceError(INVALID_OR_ADVANCE_FEE_CASHFLOW, "net received must be > 0")
    if total_repayment_vnd <= 0:
        raise FinanceError(MISSING_REPAYMENT_OUTFLOWS)
    if tenure_days < 1:
        raise FinanceError(INVALID_INPUT, "tenure_days must be >= 1")
    return (
        CashFlowPoint(day=0, amount_vnd=net_received_vnd),
        CashFlowPoint(day=tenure_days, amount_vnd=-total_repayment_vnd),
    )


def days_from_dates(disbursed_on: date, payment_dates: Sequence[date]) -> tuple[int, ...]:
    """Đổi lịch ngày dương lịch thật sang số ngày Actual/365 (S2-EC42) — không dùng 30/360."""
    days = tuple((d - disbursed_on).days for d in payment_dates)
    if any(x <= 0 for x in days) or any(b <= a for a, b in pairwise(days)):
        raise FinanceError(INVALID_INPUT, "payment dates must be strictly after disbursement")
    return days


def count_sign_changes(flows: Sequence[CashFlowPoint]) -> int:
    """Số lần đổi dấu theo quy tắc Descartes (bỏ qua 0), theo thứ tự ngày."""
    signs = [p.amount_vnd > 0 for p in sorted(flows, key=lambda p: p.day) if p.amount_vnd != 0]
    return sum(1 for a, b in pairwise(signs) if a != b)


def pmt(principal_vnd: int, period_rate: Decimal, periods: int) -> int:
    """Khoản trả đều mỗi kỳ (annuity) trên dư nợ giảm dần, làm tròn half-up về đồng."""
    if periods < 1:
        raise FinanceError(INVALID_INPUT, "periods must be >= 1")
    p = Decimal(principal_vnd)
    if period_rate == 0:
        value = p / periods
    else:
        growth = (Decimal(1) + period_rate) ** periods
        value = p * period_rate * growth / (growth - 1)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
