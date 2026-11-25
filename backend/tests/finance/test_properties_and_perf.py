"""Property test (hypothesis) + benchmark < 1ms/lần (File 6 §2 hàng 1)."""

from __future__ import annotations

import math
import statistics
import time
from collections.abc import Callable
from decimal import Decimal
from functools import partial

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.finance import (
    CashFlowPoint,
    LoanTerms,
    RepaymentRegime,
    build_cashflows,
    bullet_cashflows,
    livelihood_pain,
    pmt,
    solve_ear_xirr,
    solve_vn_legal_apr_365,
)
from app.finance.solvers import BRACKET_HI

_net = st.integers(min_value=100_000, max_value=2_000_000_000)
_days = st.integers(min_value=1, max_value=3650)


@settings(max_examples=300, deadline=None)
@given(net=_net, extra_permille=st.integers(min_value=1, max_value=5_000), days=_days)
def test_property_apr_n1_equals_simple_interest(net: int, extra_permille: int, days: int) -> None:
    """APR(n=1) == (|CF1| - CF0)/CF0 · 365/D (File 2 §4.2, trường hợp A)."""
    repay = net + net * extra_permille // 1000
    simple = (repay - net) / net * 365 / days
    assume(simple < BRACKET_HI)
    apr = solve_vn_legal_apr_365(bullet_cashflows(net, repay, days))
    assert math.isclose(apr, simple, rel_tol=1e-9, abs_tol=1e-9)


@settings(max_examples=300, deadline=None)
@given(net=_net, extra_permille=st.integers(min_value=1, max_value=5_000), days=_days)
def test_property_xirr_n1_closed_form(net: int, extra_permille: int, days: int) -> None:
    repay = net + net * extra_permille // 1000
    log_growth = math.log(repay / net) * 365 / days
    assume(log_growth < math.log1p(BRACKET_HI))
    xirr = solve_ear_xirr(bullet_cashflows(net, repay, days))
    assert math.isclose(xirr, math.expm1(log_growth), rel_tol=1e-8, abs_tol=1e-9)


@settings(max_examples=200, deadline=None)
@given(
    net=st.integers(min_value=1_000_000, max_value=500_000_000),
    extra_permille=st.integers(min_value=1, max_value=3_000),
    days=st.integers(min_value=1, max_value=364),
)
def test_property_xirr_exceeds_apr_for_short_bullets(
    net: int, extra_permille: int, days: int
) -> None:
    """Kỳ < 1 năm: ghép lãi kép luôn >= quy đổi lãi đơn (thứ tự 2 chỉ số không đảo)."""
    repay = net + net * extra_permille // 1000
    assume(math.log(repay / net) * 365 / days < math.log1p(BRACKET_HI))
    flows = bullet_cashflows(net, repay, days)
    assert solve_ear_xirr(flows) >= solve_vn_legal_apr_365(flows) - 1e-9


@settings(max_examples=100, deadline=None)
@given(
    principal=st.integers(min_value=1_000_000, max_value=200_000_000),
    rate_bp=st.integers(min_value=1, max_value=2_000),
    periods=st.integers(min_value=2, max_value=36),
)
def test_property_installment_apr_recovers_period_rate(
    principal: int, rate_bp: int, periods: int
) -> None:
    """Trả góp đều 30 ngày theo PMT(r) → APR_VN-365 ≈ r·365/30 (sai số làm tròn đồng)."""
    rate = Decimal(rate_bp) / Decimal(10_000)
    flows = build_cashflows(
        LoanTerms(
            nominal_principal_vnd=principal,
            regimes=[RepaymentRegime(periods, 30, pmt(principal, rate, periods))],
        )
    )
    assert math.isclose(solve_vn_legal_apr_365(flows), float(rate) * 365 / 30, abs_tol=1e-4)


def _median_ms(fn: Callable[[], object], runs: int = 300) -> float:
    for _ in range(20):
        fn()
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def test_benchmark_each_call_under_1ms() -> None:
    """File 6 §2 hàng 1: thời gian giải trung bình < 1ms / hợp đồng (trung vị 300 lần)."""
    bullet = bullet_cashflows(8_500_000, 12_000_000, 30)
    daily = (CashFlowPoint(0, 10_000_000), *(CashFlowPoint(d, -250_000) for d in range(1, 51)))
    monthly = (
        CashFlowPoint(0, 10_000_000),
        *(CashFlowPoint(30 * k, -1_350_000) for k in range(1, 13)),
    )
    terms = LoanTerms(nominal_principal_vnd=10_000_000, regimes=[RepaymentRegime(50, 1, 250_000)])
    for flows in (bullet, daily, monthly):
        for fn in (solve_vn_legal_apr_365, solve_ear_xirr, livelihood_pain):
            assert _median_ms(partial(fn, flows)) < 1.0, fn.__name__
    assert _median_ms(lambda: build_cashflows(terms)) < 1.0
