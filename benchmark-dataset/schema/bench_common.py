"""Shared helpers for VayShield-Bench-340 (pure, no I/O).

APR convention: XIRR on actual/365 day offsets, borrower view, result in percent.
"""

from __future__ import annotations

import re
from decimal import Decimal, getcontext

getcontext().prec = 50

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


def verdict_for(
    apr_percent: float,
    *,
    advance_fee: bool,
    spoof: bool,
    bnpl_cashout: bool,
    p2p_over_cap: bool,
) -> str:
    """Ground-truth verdict rule (documented in README)."""
    if advance_fee or spoof:
        return "BLOCK"
    if bnpl_cashout or p2p_over_cap or apr_percent > 36:
        return "DANGER"
    if apr_percent > 20:
        return "CAUTION"
    return "SAFE"


def mask_text(text: str, pii_spans: list[str], otp: str | None) -> str:
    """Reference masker: OTP -> [OTP], every PII span -> [MASKED]."""
    out = text
    if otp:
        out = out.replace(otp, "[OTP]")
    for span in sorted(pii_spans, key=len, reverse=True):
        out = out.replace(span, "[MASKED]")
    return out
