"""Bất định chữ số từ log-prob token (EC-AI15, File 3 §1.2 ``H_tok``).

Gán mỗi token chữ số trong JSON đầu ra về field chứa nó, rồi tính xác suất top-1/top-2 và
entropy (bit) trên phân phối top-k đã chuẩn hóa.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .gemini_client import TokenLogprob

CRITICAL_NUMERIC_FIELDS = frozenset(
    {
        "nominal_principal_vnd",
        "net_disbursed_vnd",
        "total_repayment_stated_vnd",
        "tenure_days_total",
        "period_count",
        "period_interval_days",
        "amount_per_period_vnd",
        "amount_vnd",
        "uncertainty_max_vnd",
    }
)

_KEY_BEFORE_NUMBER_RE = re.compile(r'"(\w+)"\s*:\s*-?[\d.]*$')
_DIGIT_RE = re.compile(r"\d")


@dataclass(frozen=True, slots=True)
class DigitUncertainty:
    field: str | None
    token: str
    top1_prob: float
    top2_prob: float
    entropy_bits: float

    @property
    def margin(self) -> float:
        return self.top1_prob - self.top2_prob


def _distribution(tok: TokenLogprob) -> list[float]:
    logps = [c.logprob for c in tok.top] or [tok.logprob]
    probs = sorted((math.exp(lp) for lp in logps), reverse=True)
    total = sum(probs)
    return [p / total for p in probs] if total > 0 else [1.0]


def digit_uncertainties(tokens: Sequence[TokenLogprob]) -> list[DigitUncertainty]:
    """Bất định của từng token chữ số nằm ngoài chuỗi JSON (tức giá trị số)."""
    out: list[DigitUncertainty] = []
    prefix: list[str] = []
    in_string = False
    escape = False
    for tok in tokens:
        if not in_string and _DIGIT_RE.search(tok.token):
            text_before = "".join(prefix)
            match = _KEY_BEFORE_NUMBER_RE.search(text_before)
            probs = _distribution(tok)
            entropy = -sum(p * math.log2(p) for p in probs if p > 0)
            out.append(
                DigitUncertainty(
                    field=match.group(1) if match else None,
                    token=tok.token,
                    top1_prob=probs[0],
                    top2_prob=probs[1] if len(probs) > 1 else 0.0,
                    entropy_bits=entropy,
                )
            )
        for ch in tok.token:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
        prefix.append(tok.token)
    return out


def critical_uncertainties(items: Sequence[DigitUncertainty]) -> list[DigitUncertainty]:
    """Chỉ giữ chữ số thuộc field trọng yếu; field không xác định coi là trọng yếu (an toàn)."""
    return [u for u in items if u.field is None or u.field in CRITICAL_NUMERIC_FIELDS]
