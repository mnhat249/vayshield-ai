"""Kiểm tra biên độ hợp lý số liệu Việt Nam (EC-AI09) và bảng nhiều gói (EC-AI08)."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from .schemas import MultiImageLoanExtraction

# Giả định: khoản vay tiêu dùng ở VN không có gốc/thực nhận/tổng trả < 100.000 đồng.
MIN_PLAUSIBLE_LOAN_VND = 100_000
THOUSAND_FACTOR = 1_000
# Giả định: tổng trả / gốc ngoài [1, 5] là bất thường → nghi ghép nhầm cột gói vay.
MAX_PLAUSIBLE_REPAY_RATIO = 5

_THOUSAND_HINTS = (
    "nghìn đồng",
    "ngàn đồng",
    "nghin dong",
    "ngan dong",
    "đơn vị: nghìn",
    "đơn vị nghìn",
    "đvt: nghìn",
    "đvt: 1.000",
    "x1000",
    "x 1000",
    "x1.000",
    "(k)",
    "nghìn vnđ",
    "k vnđ",
)
_MULTI_PACKAGE_HINTS = ("nhiều gói", "bảng so sánh", "các gói vay", "gói vay khác nhau")


def _nfkc_lower(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


@dataclass(frozen=True, slots=True)
class MagnitudeCheck:
    extraction: MultiImageLoanExtraction
    scaled_fields: tuple[str, ...]  # đã nhân 1000 — tô sáng trên thanh xác nhận
    suspicious_fields: tuple[str, ...]  # quá nhỏ, không có gợi ý đơn vị — hỏi người dùng


def _has_thousand_hint(texts: Sequence[str]) -> bool:
    joined = " ".join(_nfkc_lower(t) for t in texts)
    return any(h in joined for h in _THOUSAND_HINTS)


def apply_magnitude_sanity(
    extraction: MultiImageLoanExtraction, unit_hint_texts: Sequence[str] = ()
) -> MagnitudeCheck:
    """EC-AI09: gốc ``10.000`` + tiêu đề ``nghìn đồng`` → nhân 10^3 toàn bộ số tiền."""
    loan_fields = {
        "nominal_principal_vnd": extraction.nominal_principal_vnd,
        "net_disbursed_vnd": extraction.net_disbursed_vnd,
        "total_repayment_stated_vnd": extraction.total_repayment_stated_vnd,
    }
    too_small = tuple(
        name for name, v in loan_fields.items() if v is not None and 0 < v < MIN_PLAUSIBLE_LOAN_VND
    )
    if not too_small:
        return MagnitudeCheck(extraction, (), ())
    hints = [
        *unit_hint_texts,
        *extraction.extraction_notes_vi,
        extraction.stated_interest_rate_text or "",
    ]
    if not _has_thousand_hint(hints):
        return MagnitudeCheck(extraction, (), too_small)

    data = extraction.model_dump()
    scaled: list[str] = []
    for name in loan_fields:
        if data[name] is not None:
            data[name] *= THOUSAND_FACTOR
            scaled.append(name)
    for i, fee in enumerate(data["deducted_fees_at_source"]):
        fee["amount_vnd"] *= THOUSAND_FACTOR
        if fee["uncertainty_max_vnd"] is not None:
            fee["uncertainty_max_vnd"] *= THOUSAND_FACTOR
        scaled.append(f"deducted_fees_at_source[{i}]")
    for i, regime in enumerate(data["repayment_regimes"]):
        regime["amount_per_period_vnd"] *= THOUSAND_FACTOR
        scaled.append(f"repayment_regimes[{i}]")
    if data["upfront_fee_demand"]["amount_vnd"] is not None:
        data["upfront_fee_demand"]["amount_vnd"] *= THOUSAND_FACTOR
        scaled.append("upfront_fee_demand.amount_vnd")
    data["extraction_notes_vi"] = [
        *data["extraction_notes_vi"],
        "Số tiền trong ảnh ghi theo đơn vị nghìn đồng — đã nhân 1.000, vui lòng kiểm tra lại.",
    ]
    return MagnitudeCheck(MultiImageLoanExtraction.model_validate(data), tuple(scaled), ())


def check_package_plausibility(extraction: MultiImageLoanExtraction) -> tuple[str, ...]:
    """EC-AI08: tỷ lệ TotalRepay/Principal bất thường hoặc ghi chú bảng nhiều gói."""
    issues: list[str] = []
    principal = extraction.nominal_principal_vnd
    total = extraction.total_repayment_stated_vnd
    if principal is not None and total is not None:
        if total < principal:
            issues.append("TOTAL_BELOW_PRINCIPAL")
        elif total > MAX_PLAUSIBLE_REPAY_RATIO * principal:
            issues.append("REPAY_RATIO_IMPLAUSIBLE")
    net = extraction.net_disbursed_vnd
    if principal is not None and net is not None and net > principal:
        issues.append("NET_ABOVE_PRINCIPAL")
    notes = " ".join(_nfkc_lower(n) for n in extraction.extraction_notes_vi)
    if any(h in notes for h in _MULTI_PACKAGE_HINTS):
        issues.append("MULTI_PACKAGE_TABLE")
    return tuple(issues)
