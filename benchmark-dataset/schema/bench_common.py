"""Shared helpers for VayShield-Bench-340 (pure, no I/O).

APR convention: XIRR on actual/365 day offsets, borrower view, result in percent.
"""

from __future__ import annotations

import importlib.util
import itertools
import re
import sys
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 50

EAR_MAX_PERCENT = Decimal(50000)
SET_SIZES = {"contract": 80, "voice": 40, "coercion": 120, "notif": 100}
LONG_DIGITS = re.compile(r"\d{6,}")


def xirr_percent(cashflows: list[tuple[int, int]]) -> Decimal:
    """Annualised IRR (percent, 2 dp) of (day, amount_vnd) flows; signs may be either way."""

    def npv(rate: Decimal) -> Decimal:
        base = Decimal(1) + rate
        return sum(
            (Decimal(a) / (base ** (Decimal(d) / Decimal(365))) for d, a in cashflows),
            Decimal(0),
        )

    lo, hi = Decimal(0), Decimal(100000)  # up to 10,000,000 %
    f_lo = npv(lo)
    if f_lo == 0:
        return Decimal("0.00")
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return ((lo + hi) / 2 * 100).quantize(Decimal("0.01"))


def apr_vn_365_percent(cashflows: list[tuple[int, int]]) -> Decimal:
    """APR_VN-365 (File 2 s4.2, case B; reduces to simple interest when n = 1), percent, 2 dp.

    Solves CF0 + sum_k CF_k / prod_{j<=k} (1 + r * (t_j - t_{j-1}) / 365) = 0 by bisection
    on r in [0, 500] (up to 50,000 %/year). Needs CF0 > 0 (disbursed) and repayments < 0.
    """
    flows = sorted(cashflows)
    if not flows or flows[0][0] != 0 or flows[0][1] <= 0:
        raise ValueError("INVALID_OR_ADVANCE_FEE_CASHFLOW")

    def f(r: Decimal) -> Decimal:
        total = Decimal(flows[0][1])
        discount = Decimal(1)
        for (t_prev, _), (t, amount) in itertools.pairwise(flows):
            discount *= Decimal(1) + r * Decimal(t - t_prev) / Decimal(365)
            total += Decimal(amount) / discount
        return total

    lo, hi = Decimal(0), Decimal(500)
    if f(lo) >= 0:  # repays no more than principal -> 0 % interest
        return Decimal("0.00")
    for _ in range(200):  # f is increasing in r (repayments shrink as r grows)
        mid = (lo + hi) / 2
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return ((lo + hi) / 2 * 100).quantize(Decimal("0.01"))


def ear_xirr_percent_or_none(cashflows: list[tuple[int, int]]) -> Decimal | None:
    """EAR_XIRR (File 2 s4.2 index 2); None when above the 50,000 %/year solver bracket."""
    v = xirr_percent(cashflows)
    return None if v > EAR_MAX_PERCENT else v


def verdict_for(
    apr_vn_365: float | None,
    *,
    advance_fee: bool,
    spoof: bool,
    bnpl_or_straw: bool,
    licensed: bool,
    p2p_over_cap: bool,
    pawn: bool,
    incomplete_doc: bool,
) -> str:
    """Ground-truth verdict per File 2 s4.4 (rules evaluated in table order)."""
    if advance_fee:
        return "V_RED_ADVANCE_FEE"
    if spoof:
        return "V_RED_BRAND_SPOOF"
    if bnpl_or_straw:
        return "V_RED_BNPL_OR_STRAW_SCAM"
    assert apr_vn_365 is not None
    if not licensed and apr_vn_365 >= 100:
        return "V_RED_CRIMINAL_USURY"
    if (p2p_over_cap or not licensed) and not pawn and 20 < apr_vn_365 < 100:
        return "V_ORANGE_CIVIL_CAP_EXCEEDED"
    if pawn and apr_vn_365 > 35:
        return "V_ORANGE_PAWN_HIGH_FEE"
    if incomplete_doc:
        return "V_AMBER_INCOMPLETE_DOC"
    if licensed and apr_vn_365 > 35:
        return "V_YELLOW_HIGH_COST_LICENSED"
    return "V_GREEN_TRANSPARENT"


def spec_verdict_codes() -> set[str]:
    """The 9 verdict codes, loaded from backend/app/core/verdict_codes.py (single source)."""
    path = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "app"
        / "core"
        / "verdict_codes.py"
    )
    spec = importlib.util.spec_from_file_location("_vs_verdict_codes", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_vs_verdict_codes"] = (
        mod  # dataclass/enum machinery needs it registered
    )
    spec.loader.exec_module(mod)
    return {m.value for m in mod.VerdictCode}


def mask_text(text: str, pii_spans: list[str], otp: str | None) -> str:
    """Reference masker: OTP -> [OTP], every PII span -> [MASKED]."""
    out = text
    if otp:
        out = out.replace(otp, "[OTP]")
    for span in sorted(pii_spans, key=len, reverse=True):
        out = out.replace(span, "[MASKED]")
    return out
