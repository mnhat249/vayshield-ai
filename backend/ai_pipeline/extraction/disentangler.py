"""Tách đa khoản vay & hợp nhất đa ảnh (``Multi-Loan Disentangler``, File 3 §2.3).

1. EC-AI07: ≥ 2 thương hiệu → tách thẻ riêng, KHÔNG gộp chéo số liệu, yêu cầu người dùng chọn.
2. Thứ bậc màn hình: SUSPICIOUS_RECEIPT ≻ CHAT ≻ DISBURSEMENT_SUMMARY ≻ CONTRACT_TERMS ≻
   MARKETING_OFFER. Khóa dòng tiền theo màn hình cao nhất; lệch so với quảng cáo →
   ``bait_and_switch_detected``.
3. S2-EC21: khử trùng phí theo ``(nhãn chuẩn hóa, số tiền chính xác)`` giữa các ảnh — một khoản
   phí xuất hiện ở 2 ảnh cuộn chồng lấp chỉ tính 1 lần.

Nguyên tắc D2: hợp nhất không bao giờ hạ cờ cảnh báo (cờ OR, độ tin cậy lấy min).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .schemas import (
    DeductedFeeItem,
    MultiImageLoanExtraction,
    ScreenCategory,
    SpecialScamMorphology,
)

SCREEN_AUTHORITY: dict[ScreenCategory, int] = {
    ScreenCategory.SUSPICIOUS_NOTICE_RECEIPT: 5,
    ScreenCategory.CHAT_CONVERSATION: 4,
    ScreenCategory.DISBURSEMENT_SUMMARY: 3,
    ScreenCategory.CONTRACT_TERMS: 2,
    ScreenCategory.VOICE_DICTATED_TERMS: 2,  # giả định: ngang điều khoản hợp đồng
    ScreenCategory.MARKETING_OFFER: 1,
    ScreenCategory.NON_LOAN_IRRELEVANT: 0,
}
# Alias đã biết (khóa đã chuẩn hóa, bỏ khoảng trắng) → khóa chuẩn. KHÔNG dùng so khớp mờ:
# "App Vay Nhanh A" và "App Vay Nhanh B" khác 1 ký tự nhưng là 2 khoản vay khác nhau (EC-AI07).
BRAND_ALIASES: dict[str, str] = {
    "fecredit": "fecredit",
    "fecreditvn": "fecredit",
    "homecredit": "homecredit",
    "homecreditvietnam": "homecredit",
    "tiachop": "tiachop",
    "apptiachop": "tiachop",
}

_LOCKED_SCALARS = (
    "nominal_principal_vnd",
    "net_disbursed_vnd",
    "total_repayment_stated_vnd",
    "tenure_days_total",
    "stated_interest_rate_text",
    "late_penalty_text",
    "claimed_lender_name",
    "claimed_company_legal_name",
)
_BAIT_COMPARE = (
    "nominal_principal_vnd",
    "net_disbursed_vnd",
    "total_repayment_stated_vnd",
    "tenure_days_total",
)
_OR_FLAGS = (
    "is_loan_extension_rollover",
    "bait_and_switch_detected",
    "missing_critical_page_suspected",
)


def normalize_label(text: str) -> str:
    """Bỏ dấu, chữ thường, chỉ giữ chữ-số: ``"Phí  Hồ sơ:"`` ≡ ``"phi ho so"``."""
    decomposed = unicodedata.normalize("NFD", unicodedata.normalize("NFKC", text).casefold())
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    stripped = stripped.replace("đ", "d")
    return " ".join("".join(c if c.isalnum() else " " for c in stripped).split())


def brand_key(name: str) -> str:
    """Khóa thương hiệu: bỏ dấu, chữ thường, bỏ khoảng trắng/ký tự đặc biệt, qua alias.

    "FE Credit" ≡ "fe-credit" ≡ "FECREDIT"; "Tia Chớp" ≡ "Tia Chop"; nhưng "... A" ≠ "... B".
    """
    key = normalize_label(name).replace(" ", "")
    return BRAND_ALIASES.get(key, key)


def page_authority(extraction: MultiImageLoanExtraction) -> int:
    return max((SCREEN_AUTHORITY[c] for c in extraction.screen_categories), default=0)


@dataclass(frozen=True, slots=True)
class PageExtraction:
    page_index: int
    extraction: MultiImageLoanExtraction


@dataclass(frozen=True, slots=True)
class LoanCard:
    brand: str | None
    page_indices: tuple[int, ...]
    extraction: MultiImageLoanExtraction
    locked_from_screen: ScreenCategory | None
    duplicate_fees_removed: int
    ambiguous_multi_brand_page: bool = False  # 1 ảnh chứa ≥ 2 thương hiệu — không tách được số


@dataclass(frozen=True, slots=True)
class DisentangleResult:
    cards: tuple[LoanCard, ...]
    requires_loan_selection: bool
    irrelevant_pages: tuple[int, ...]


def dedupe_fees(
    fee_lists: Sequence[Sequence[DeductedFeeItem]],
) -> tuple[list[DeductedFeeItem], int]:
    """S2-EC21: hợp nhất theo ``(nhãn chuẩn hóa, số tiền)``; số lần = max số lần trong 1 ảnh."""
    merged: dict[tuple[str, int], list[DeductedFeeItem]] = {}
    total_in = 0
    for fees in fee_lists:
        per_page: dict[tuple[str, int], list[DeductedFeeItem]] = {}
        for fee in fees:
            total_in += 1
            per_page.setdefault((normalize_label(fee.fee_label), fee.amount_vnd), []).append(fee)
        for key, items in per_page.items():
            existing = merged.setdefault(key, [])
            for idx, item in enumerate(items):
                if idx < len(existing):
                    existing[idx] = _merge_fee(existing[idx], item)
                else:
                    existing.append(item)
    out = [fee for items in merged.values() for fee in items]
    return out, total_in - len(out)


def _merge_fee(a: DeductedFeeItem, b: DeductedFeeItem) -> DeductedFeeItem:
    uncertain = [v for v in (a.uncertainty_max_vnd, b.uncertainty_max_vnd) if v is not None]
    return DeductedFeeItem(
        fee_label=a.fee_label,
        amount_vnd=a.amount_vnd,
        uncertainty_max_vnd=max(uncertain) if uncertain else None,
        is_mandatory_or_prechecked=a.is_mandatory_or_prechecked or b.is_mandatory_or_prechecked,
    )


def merge_pages(
    pages: Sequence[PageExtraction],
) -> tuple[MultiImageLoanExtraction, ScreenCategory | None, int]:
    """Hợp nhất các ảnh CÙNG một khoản vay theo thứ bậc màn hình."""
    if not pages:
        raise ValueError("cần ít nhất 1 ảnh")
    ordered = sorted(pages, key=lambda p: (-page_authority(p.extraction), p.page_index))
    exts = [p.extraction for p in ordered]
    data: dict[str, Any] = exts[0].model_dump()

    locked_from: ScreenCategory | None = None
    for name in _LOCKED_SCALARS:
        data[name] = next((getattr(e, name) for e in exts if getattr(e, name) is not None), None)
    for e in exts:
        if e.net_disbursed_vnd is not None or e.nominal_principal_vnd is not None:
            locked_from = max(e.screen_categories, key=lambda c: SCREEN_AUTHORITY[c])
            break
    data["repayment_regimes"] = next(
        ([r.model_dump() for r in e.repayment_regimes] for e in exts if e.repayment_regimes), []
    )

    fees, removed = dedupe_fees([e.deducted_fees_at_source for e in exts])
    data["deducted_fees_at_source"] = [f.model_dump() for f in fees]

    demanded = [e for e in exts if e.upfront_fee_demand.demanded]
    upfront = (demanded[0] if demanded else exts[0]).upfront_fee_demand.model_dump()
    upfront["fake_frozen_receipt_claimed"] = any(
        e.upfront_fee_demand.fake_frozen_receipt_claimed for e in exts
    )
    data["upfront_fee_demand"] = upfront

    for flag in _OR_FLAGS:
        data[flag] = any(getattr(e, flag) for e in exts)
    # Cầm cố chỉ tin khi màn hình hợp đồng khẳng định (cờ này có thể làm nhẹ cảnh báo).
    data["is_pawn_asset_secured"] = any(
        e.is_pawn_asset_secured and ScreenCategory.CONTRACT_TERMS in e.screen_categories
        for e in exts
    )
    data["special_morphology"] = next(
        (
            e.special_morphology
            for e in exts
            if e.special_morphology is not SpecialScamMorphology.NONE
        ),
        SpecialScamMorphology.NONE,
    )

    # Bait-and-switch: màn hình quảng cáo lệch so với số đã khóa.
    for p in pages:
        if page_authority(p.extraction) != SCREEN_AUTHORITY[ScreenCategory.MARKETING_OFFER]:
            continue
        for name in _BAIT_COMPARE:
            offer, locked = getattr(p.extraction, name), data[name]
            if offer is not None and locked is not None and offer != locked:
                data["bait_and_switch_detected"] = True

    data["screen_categories"] = list(
        dict.fromkeys(
            c
            for p in sorted(pages, key=lambda p: p.page_index)
            for c in p.extraction.screen_categories
        )
    )
    data["distinct_loan_brands_detected"] = list(
        dict.fromkeys(b for e in exts for b in e.distinct_loan_brands_detected)
    )
    data["critical_fields_min_confidence"] = min(e.critical_fields_min_confidence for e in exts)
    data["extraction_notes_vi"] = list(
        dict.fromkeys(n for e in exts for n in e.extraction_notes_vi)
    )
    return MultiImageLoanExtraction.model_validate(data), locked_from, removed


def _page_brands(e: MultiImageLoanExtraction) -> list[str]:
    names = [*e.distinct_loan_brands_detected]
    if e.claimed_lender_name:
        names.append(e.claimed_lender_name)
    out: list[str] = []
    for n in names:
        key = brand_key(n)
        if key and not any(key == brand_key(o) for o in out):
            out.append(n)
    return out


class MultiLoanDisentangler:
    def disentangle(self, pages: Sequence[PageExtraction]) -> DisentangleResult:
        irrelevant = tuple(
            p.page_index
            for p in pages
            if set(p.extraction.screen_categories) <= {ScreenCategory.NON_LOAN_IRRELEVANT}
        )
        relevant = [p for p in pages if p.page_index not in irrelevant]
        groups: list[tuple[str, list[PageExtraction]]] = []
        ambiguous: list[PageExtraction] = []
        unbranded: list[PageExtraction] = []
        for p in relevant:
            brands = _page_brands(p.extraction)
            if len(brands) >= 2:
                ambiguous.append(p)
                continue
            if not brands:
                unbranded.append(p)
                continue
            key = brand_key(brands[0])
            for g_key, g_pages in groups:
                if key == brand_key(g_key):
                    g_pages.append(p)
                    break
            else:
                groups.append((brands[0], [p]))

        cards: list[LoanCard] = []
        if len(groups) <= 1 and not ambiguous:
            # Một thương hiệu (hoặc không rõ): ảnh không tên thuộc cùng khoản vay.
            brand = groups[0][0] if groups else None
            members = (groups[0][1] if groups else []) + unbranded
            if members:
                cards.append(self._card(brand, members))
        else:
            for brand, members in groups:
                cards.append(self._card(brand, members))
            # Ảnh không tên khi có ≥ 2 thương hiệu: không đoán gán, tách thẻ riêng.
            cards.extend(self._card(None, [p]) for p in unbranded)
            cards.extend(self._card(None, [p], ambiguous=True) for p in ambiguous)
        requires = len(cards) >= 2 or any(c.ambiguous_multi_brand_page for c in cards)
        return DisentangleResult(tuple(cards), requires, irrelevant)

    @staticmethod
    def _card(
        brand: str | None, members: Sequence[PageExtraction], *, ambiguous: bool = False
    ) -> LoanCard:
        merged, locked, removed = merge_pages(members)
        return LoanCard(
            brand=brand,
            page_indices=tuple(sorted(p.page_index for p in members)),
            extraction=merged,
            locked_from_screen=locked,
            duplicate_fees_removed=removed,
            ambiguous_multi_brand_page=ambiguous,
        )


# --- Khoảng phí [best_case, worst_case] (EC-AI24, File 3 §1.2 cuối) ------------

_OPTIONAL_ADDON_HINTS = ("bao hiem", "goi ", "dich vu", "thanh vien", "vip", "insurance")


@dataclass(frozen=True, slots=True)
class FeeInterval:
    best_case_vnd: int
    worst_case_vnd: int


def deducted_fee_interval(fees: Sequence[DeductedFeeItem]) -> FeeInterval:
    """Cận phí khấu trừ: worst gồm mọi khoản bắt buộc/tích sẵn ở cận trên; best bỏ khoản
    cộng thêm tích sẵn (bảo hiểm, gói dịch vụ) vì người vay có thể bỏ tích."""
    best = 0
    worst = 0
    for fee in fees:
        if not fee.is_mandatory_or_prechecked:
            continue
        upper = max(fee.amount_vnd, fee.uncertainty_max_vnd or 0)
        worst += upper
        label = normalize_label(fee.fee_label) + " "
        if not any(h in label for h in _OPTIONAL_ADDON_HINTS):
            best += fee.amount_vnd
    return FeeInterval(best, worst)
