"""Bộ giải lãi suất tất định (File 2 §4.2): APR_VN-365, EAR_XIRR, khoảng bất định, MIRR.

Mọi lãi suất trả về là **phân số/năm** (5.0098 = 500,98%/năm). Tầng API nhân 100 khi điền
các trường ``apr_*``/``ear_xirr`` (%/năm) của contract.

Thuật toán: Brent (nội suy nghịch đảo bậc 2 + secant + chia đôi bảo vệ) trên khoảng kẹp
``[-0.99, 500]``. Khi vector CF đổi dấu > 1 lần (Descartes), quét lưới để phát hiện đa nghiệm
và bổ sung MIRR với lãi suất tái đầu tư 4,5%/năm (S2-EC40).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Final

from app.finance.cashflow import CashFlowPoint, count_sign_changes
from app.finance.errors import (
    INVALID_INPUT,
    INVALID_OR_ADVANCE_FEE_CASHFLOW,
    MISSING_REPAYMENT_OUTFLOWS,
    MULTIPLE_IRR_ROOTS,
    NON_MONOTONIC_OR_EXTREME_CASHFLOW,
    FinanceError,
    MultipleRootsError,
    RateOutOfBracketError,
)

__all__ = [
    "BRACKET_HI",
    "BRACKET_LO",
    "DEFAULT_REINVEST_RATE",
    "LegalCapPosition",
    "RateReport",
    "analyze_cashflows",
    "brent_root",
    "compare_to_legal_cap",
    "interval_crosses",
    "npv_legal_365",
    "npv_xirr",
    "solve_apr_interval_bounds",
    "solve_ear_xirr",
    "solve_mirr",
    "solve_vn_legal_apr_365",
    "stated_rate_contradiction",
]

BRACKET_LO: Final = -0.99
BRACKET_HI: Final = 500.0
DEFAULT_REINVEST_RATE: Final = Decimal("0.045")
LEGAL_BOUNDARY_TOLERANCE: Final = 0.005  # ±0,5 điểm % tại mốc 20% (S2-EC42)
_XTOL: Final = 1e-12
_RTOL: Final = 4 * 2.220446049250313e-16
_MAX_ITER: Final = 100


def _validate(flows: Sequence[CashFlowPoint]) -> tuple[CashFlowPoint, ...]:
    # Bỏ điểm 0đ ở t > 0 (kỳ ân hạn không trả): mỗi điểm thừa sẽ nối thêm 1 hệ số lãi đơn
    # và làm APR lệch so với vector đã gộp của ``build_cashflows``.
    ordered = tuple(
        sorted((p for p in flows if p.day == 0 or p.amount_vnd != 0), key=lambda p: p.day)
    )
    if not ordered or ordered[0].day != 0 or ordered[0].amount_vnd <= 0:
        raise FinanceError(INVALID_OR_ADVANCE_FEE_CASHFLOW)
    for a, b in pairwise(ordered):
        if b.day == a.day:
            raise FinanceError(INVALID_INPUT, "duplicate day in cashflow")
    if not any(p.amount_vnd < 0 for p in ordered[1:]):
        raise FinanceError(MISSING_REPAYMENT_OUTFLOWS)
    return ordered


def npv_legal_365(flows: Sequence[CashFlowPoint], r_annual: float) -> float:
    """NPV theo chuẩn TT 18/2019: chiết khấu lãi đơn nối kỳ ``prod(1 + r·Δt/365)``."""
    total = float(flows[0].amount_vnd)
    factor = 1.0
    prev = flows[0].day
    for p in flows[1:]:
        step = 1.0 + r_annual * ((p.day - prev) / 365.0)
        if step <= 1e-12:
            return -math.inf
        factor *= step
        total += p.amount_vnd / factor
        prev = p.day
    return total


def npv_xirr(flows: Sequence[CashFlowPoint], r_annual: float) -> float:
    """NPV chuẩn XIRR: ``sum CF_k / (1+r)^(t_k/365)``."""
    if r_annual <= -1.0:
        return -math.inf
    log_base = math.log1p(r_annual)
    total = 0.0
    for p in flows:
        total += p.amount_vnd * math.exp(-log_base * p.day / 365.0)
    return total


def brent_root(
    f: Callable[[float], float],
    a: float,
    b: float,
    *,
    xtol: float = _XTOL,
    rtol: float = _RTOL,
    max_iter: int = _MAX_ITER,
) -> tuple[float, int]:
    """Phương pháp Brent (theo brentq). Yêu cầu ``f(a)·f(b) <= 0``. Trả ``(nghiệm, số vòng)``."""
    fa, fb = f(a), f(b)
    if fa == 0.0:
        return a, 0
    if fb == 0.0:
        return b, 0
    if fa * fb > 0:
        raise FinanceError(INVALID_INPUT, "root not bracketed")
    c, fc = a, fa
    d = e = b - a
    for i in range(1, max_iter + 1):
        if fb * fc > 0:
            c, fc = a, fa
            d = e = b - a
        if abs(fc) < abs(fb):
            a, b, c = b, c, b
            fa, fb, fc = fb, fc, fb
        tol = 2.0 * rtol * abs(b) + 0.5 * xtol
        m = 0.5 * (c - b)
        if fb == 0.0 or abs(m) <= tol:
            return b, i
        if abs(e) >= tol and abs(fa) > abs(fb):
            s = fb / fa
            if a == c:
                p = 2.0 * m * s
                q = 1.0 - s
            else:
                q0 = fa / fc
                r0 = fb / fc
                p = s * (2.0 * m * q0 * (q0 - r0) - (b - a) * (r0 - 1.0))
                q = (q0 - 1.0) * (r0 - 1.0) * (s - 1.0)
            if p > 0:
                q = -q
            else:
                p = -p
            if 2.0 * p < min(3.0 * m * q - abs(tol * q), abs(e * q)):
                e, d = d, p / q
            else:
                d = e = m
        else:
            d = e = m
        a, fa = b, fb
        b += d if abs(d) > tol else math.copysign(tol, m)
        fb = f(b)
    raise FinanceError(NON_MONOTONIC_OR_EXTREME_CASHFLOW, "brent did not converge")


def _scan_grid(lo: float, hi: float) -> list[float]:
    pts = [lo + (0.0 - lo) * k / 20 for k in range(20)]
    x = 0.005
    while x < hi:
        pts.append(x)
        x *= 1.35
    pts.append(hi)
    return pts


def _solve(
    flows: Sequence[CashFlowPoint],
    npv: Callable[[Sequence[CashFlowPoint], float], float],
    lo: float,
) -> tuple[float, int]:
    ordered = _validate(flows)

    def f(r: float) -> float:
        return npv(ordered, r)

    if count_sign_changes(ordered) > 1:
        grid = _scan_grid(lo, BRACKET_HI)
        values = [f(x) for x in grid]
        brackets = [
            (grid[i], grid[i + 1])
            for i in range(len(grid) - 1)
            if values[i] == 0.0 or values[i] * values[i + 1] < 0
        ]
        if len(brackets) > 1:
            roots = tuple(brent_root(f, a, b)[0] for a, b in brackets)
            raise MultipleRootsError(roots)
        if len(brackets) == 1:
            return brent_root(f, *brackets[0])
        raise RateOutOfBracketError("above" if values[-1] < 0 else "below", BRACKET_HI)

    f_lo, f_hi = f(lo), f(BRACKET_HI)
    if f_hi < 0:
        raise RateOutOfBracketError("above", BRACKET_HI)
    if f_lo > 0:
        raise RateOutOfBracketError("below", lo)
    return brent_root(f, lo, BRACKET_HI)


def _legal_lower_bound(flows: Sequence[CashFlowPoint]) -> float:
    ordered = sorted(flows, key=lambda p: p.day)
    max_gap = max((b.day - a.day for a, b in pairwise(ordered)), default=1)
    # Giữ mọi hệ số 1 + r·Δt/365 >= 0.01 để hàm liên tục trên khoảng kẹp.
    return max(BRACKET_LO, -0.99 * 365.0 / max(max_gap, 1))


def solve_vn_legal_apr_365_with_iterations(flows: Sequence[CashFlowPoint]) -> tuple[float, int]:
    """Như ``solve_vn_legal_apr_365`` nhưng trả thêm số vòng Brent (để kiểm < 45 vòng)."""
    ordered = _validate(flows)  # khoảng kẹp tính trên vector đã bỏ điểm 0đ
    return _solve(ordered, npv_legal_365, _legal_lower_bound(ordered))


def solve_vn_legal_apr_365(flows: Sequence[CashFlowPoint]) -> float:
    """APR_VN-365 thống nhất (bullet: trùng lãi đơn ``(|CF1|-CF0)/CF0·365/D``; trả góp: dư nợ
    giảm dần cơ sở 365 ngày)."""
    return solve_vn_legal_apr_365_with_iterations(flows)[0]


def solve_ear_xirr_with_iterations(flows: Sequence[CashFlowPoint]) -> tuple[float, int]:
    return _solve(flows, npv_xirr, BRACKET_LO)


def solve_ear_xirr(flows: Sequence[CashFlowPoint]) -> float:
    """EAR_XIRR ghép lãi kép theo năm (Actual/365)."""
    return solve_ear_xirr_with_iterations(flows)[0]


def solve_apr_interval_bounds(
    flows_best_case: Sequence[CashFlowPoint],
    flows_worst_case: Sequence[CashFlowPoint],
    *more_cases: Sequence[CashFlowPoint],
) -> tuple[float, float]:
    """Khoảng bất định đóng ``[APR_min, APR_max]`` qua các kịch bản OCR (S2-EC41)."""
    aprs = [solve_vn_legal_apr_365(c) for c in (flows_best_case, flows_worst_case, *more_cases)]
    return (min(aprs), max(aprs))


def interval_crosses(bounds: tuple[float, float], threshold: float) -> bool:
    """True nếu ngưỡng pháp lý nằm lọt trong khoảng bất định (cần người dùng xác nhận)."""
    lo, hi = bounds
    return lo < threshold <= hi


def solve_mirr(
    flows: Sequence[CashFlowPoint],
    reinvest_rate: Decimal = DEFAULT_REINVEST_RATE,
    finance_rate: Decimal | None = None,
) -> float:
    """Modified IRR (năm, ghép lãi Actual/365) từ góc nhìn chi phí vốn của người vay.

    Đổi dấu sang góc người cho vay: dòng ra của người cho vay (giải ngân, hoàn phí) chiết khấu
    về ``t_0`` theo ``finance_rate``; dòng vào (tiền người vay trả) dồn tới ``t_n`` theo
    ``reinvest_rate`` (mặc định 4,5%/năm tiền gửi). ``MIRR = (FV/PV)^(365/t_n) - 1``.
    """
    ordered = _validate(flows)
    rr = float(reinvest_rate)
    fr = float(finance_rate if finance_rate is not None else reinvest_rate)
    if rr <= -1.0 or fr <= -1.0:
        raise FinanceError(INVALID_INPUT, "rates must be > -1")
    horizon = ordered[-1].day
    if horizon <= 0:
        raise FinanceError(INVALID_INPUT, "horizon must be > 0")
    pv_out = 0.0
    fv_in = 0.0
    for p in ordered:
        lender_amount = -p.amount_vnd
        if lender_amount < 0:
            pv_out += -lender_amount / (1.0 + fr) ** (p.day / 365.0)
        elif lender_amount > 0:
            fv_in += lender_amount * (1.0 + rr) ** ((horizon - p.day) / 365.0)
    if pv_out <= 0 or fv_in <= 0:
        raise FinanceError(MISSING_REPAYMENT_OUTFLOWS)
    return float((fv_in / pv_out) ** (365.0 / horizon) - 1.0)


class LegalCapPosition(StrEnum):
    BELOW = "BELOW"
    AT_BOUNDARY = "AT_BOUNDARY"
    ABOVE = "ABOVE"


def compare_to_legal_cap(
    apr: float, cap: float = 0.20, tolerance: float = LEGAL_BOUNDARY_TOLERANCE
) -> LegalCapPosition:
    """So APR với trần pháp lý kèm dung sai biên (S2-EC42). Vùng ``|apr-cap| <= tol`` không
    kết luận vượt trần để tránh báo oan do dời ngày nghỉ."""
    if apr > cap + tolerance:
        return LegalCapPosition.ABOVE
    if apr < cap - tolerance:
        return LegalCapPosition.BELOW
    return LegalCapPosition.AT_BOUNDARY


def stated_rate_contradiction(
    stated_annual_rate: float, actual_apr: float, tolerance: float = LEGAL_BOUNDARY_TOLERANCE
) -> bool:
    """S2-EC13: % quảng cáo (vd flat theo tháng ×12) lệch APR thực trả quá dung sai."""
    return abs(actual_apr - stated_annual_rate) > tolerance


APR_REASON_MULTIPLE_ROOTS: Final = MULTIPLE_IRR_ROOTS
APR_REASON_ABOVE_BRACKET: Final = "RATE_ABOVE_BRACKET"
APR_REASON_BELOW_BRACKET: Final = "RATE_BELOW_BRACKET"
APR_REASON_NOT_CONVERGED: Final = "SOLVER_NOT_CONVERGED"


@dataclass(frozen=True, slots=True)
class RateReport:
    """Tổng hợp cho tầng phán quyết. ``None`` = không xác định duy nhất / ngoài khoảng kẹp.

    Quy ước cho T09 (phán quyết):

    - Dùng ``apr_primary`` làm APR_VN-365 để so trần. ``apr_primary`` là nghiệm duy nhất nếu
      dòng tiền có đúng 1 nghiệm; khi đa nghiệm (S2-EC40) là nghiệm nhỏ nhất ``>= 0`` trong
      ``apr_roots`` (cận dưới — kết luận vượt trần vẫn an toàn).
    - Nếu ``apr_primary is None`` → dùng ``mirr`` (luôn được tính khi đổi dấu > 1 lần), hoặc
      ``apr_above_bracket`` (lãi > cận trên kẹp). Lý do nằm ở ``apr_unavailable_reason``.

    ``apr_unavailable_reason`` giải thích vì sao ``apr_vn_365`` (nghiệm duy nhất) là ``None``:
    ``MULTIPLE_IRR_ROOTS`` | ``RATE_ABOVE_BRACKET`` | ``RATE_BELOW_BRACKET`` (dòng tiền suy
    biến) | ``SOLVER_NOT_CONVERGED``; ``None`` khi ``apr_vn_365`` xác định.
    """

    apr_vn_365: float | None
    ear_xirr: float | None
    mirr: float | None
    sign_changes: int
    multiple_roots: bool
    apr_above_bracket: bool
    apr_roots: tuple[float, ...] = ()
    apr_primary: float | None = None
    apr_unavailable_reason: str | None = None


def _primary_root(roots: tuple[float, ...]) -> float | None:
    if len(roots) == 1:
        return roots[0]
    non_negative = [r for r in roots if r >= 0.0]
    return min(non_negative) if non_negative else None


def analyze_cashflows(
    flows: Sequence[CashFlowPoint], reinvest_rate: Decimal = DEFAULT_REINVEST_RATE
) -> RateReport:
    ordered = _validate(flows)
    sign_changes = count_sign_changes(ordered)
    multiple = False
    above = False
    apr: float | None = None
    xirr: float | None
    roots: tuple[float, ...] = ()
    reason: str | None = None
    try:
        apr = solve_vn_legal_apr_365(ordered)
        roots = (apr,)
    except MultipleRootsError as exc:
        multiple, roots = True, tuple(sorted(exc.roots))
        reason = APR_REASON_MULTIPLE_ROOTS
    except RateOutOfBracketError as exc:
        above = exc.direction == "above"
        reason = APR_REASON_ABOVE_BRACKET if above else APR_REASON_BELOW_BRACKET
    except FinanceError as exc:
        if exc.code != NON_MONOTONIC_OR_EXTREME_CASHFLOW:
            raise
        reason = APR_REASON_NOT_CONVERGED
    try:
        xirr = solve_ear_xirr(ordered)
    except MultipleRootsError:
        xirr, multiple = None, True
    except FinanceError as exc:
        if exc.code != NON_MONOTONIC_OR_EXTREME_CASHFLOW:
            raise
        xirr = None
    mirr = solve_mirr(ordered, reinvest_rate) if sign_changes > 1 else None
    return RateReport(
        apr_vn_365=apr,
        ear_xirr=xirr,
        mirr=mirr,
        sign_changes=sign_changes,
        multiple_roots=multiple,
        apr_above_bracket=above,
        apr_roots=roots,
        apr_primary=_primary_root(roots),
        apr_unavailable_reason=reason,
    )
