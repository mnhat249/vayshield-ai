"""Phí khấu trừ tại nguồn → cận phí best/worst cho khoảng nhạy APR (EC-AI24, S2-EC41).

- Best case: chỉ phí bắt buộc không phải bảo hiểm (bảo hiểm tích sẵn người vay có quyền bỏ chọn),
  lấy số đọc được ``amount_vnd``.
- Worst case: mọi phí bắt buộc HOẶC ô tích sẵn (dark pattern ``is_mandatory_or_prechecked``),
  lấy ``max(amount_vnd, uncertainty_max_vnd)`` khi chữ số bị lóa.

Phí không tích sẵn (người vay chưa chọn mua) không vào kịch bản nào.
T11 dùng 2 cận này dựng 2 vector CF rồi gọi ``analyze_cashflows`` cho từng vector.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.verdict.inputs import DeductedFee
from app.verdict.lookalike import normalize_text

__all__ = ["DeductedFeeBounds", "deducted_fee_bounds", "has_bundled_insurance", "is_insurance_fee"]

_INSURANCE_MARKERS: Final = ("bao hiem", "insurance", "bhsk", "bhkv")


def is_insurance_fee(fee: DeductedFee) -> bool:
    if fee.is_insurance is not None:
        return fee.is_insurance
    label = normalize_text(fee.label)
    return any(marker in label for marker in _INSURANCE_MARKERS)


def has_bundled_insurance(fees: Sequence[DeductedFee]) -> bool:
    """Có phí bảo hiểm bị tính vào khoản vay (bắt buộc hoặc tích sẵn) — EC-AI24."""
    return any(f.is_mandatory_or_prechecked and is_insurance_fee(f) for f in fees)


@dataclass(frozen=True, slots=True)
class DeductedFeeBounds:
    best_case_vnd: int
    worst_case_vnd: int


def deducted_fee_bounds(fees: Sequence[DeductedFee]) -> DeductedFeeBounds:
    best = 0
    worst = 0
    for fee in fees:
        if not fee.is_mandatory_or_prechecked:
            continue
        worst += max(fee.amount_vnd, fee.uncertainty_max_vnd or 0)
        if not is_insurance_fee(fee):
            best += fee.amount_vnd
    return DeductedFeeBounds(best_case_vnd=best, worst_case_vnd=worst)
