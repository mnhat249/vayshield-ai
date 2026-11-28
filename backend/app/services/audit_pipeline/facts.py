"""``LoanFacts``: dữ kiện khoản vay đã chuẩn hóa, độc lập với schema LLM (File 3 §2.2).

Cầu nối duy nhất giữa ``MultiImageLoanExtraction`` (T04) và kernel tài chính + ma trận phán quyết
(T02/T09). Đây cũng là thứ được lưu (đã che PII) trong ``contract_audits.masked_extraction_json``
để ``/recalculate`` chạy lại 0-LLM. Tiền luôn là int đồng.

Quy tắc an toàn (D2 — LLM không hạ phán quyết):

- Cờ cảnh báo (phí ứng trước, mạo danh, hình thái đặc biệt, thiếu trang, nhiều khoản vay) chỉ có
  thể được GIỮ hoặc NÂNG khi người dùng sửa số (``with_user_numbers``); không có đường nào xóa.
- ``ambiguous`` (T04: ``needs_user_confirmation`` / số bị co-giãn đơn vị / nghi ngờ / cụt JSON) ép
  khoảng APR thành "không xác định" → ma trận ra AMBER và hỏi lại người dùng, KHÔNG đoán số.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

from ai_pipeline.extraction.schemas import (
    MultiImageLoanExtraction,
    ScreenCategory,
)
from app.services.audit_pipeline.masking import mask_optional
from app.verdict.inputs import SpecialMorphology

FACTS_VERSION: Final = 1
MAX_FEES: Final = 20
MAX_REGIMES: Final = 12
MAX_NOTES: Final = 10

_COMPANY_MARKERS: Final = re.compile(
    r"(?i)\b(cong\s*ty|c\.?ty|cty|tnhh|mtv|jsc|co\.?\s*ltd|ltd|cp|cổ\s*phần|công\s*ty|"
    r"ngan\s*hang|ngân\s*hàng|bank|tài\s*chính|tai\s*chinh|fintech|vi\s*dien\s*tu|ví\s*điện\s*tử)\b"
)
_RATE_RE: Final = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*%\s*(?:/|một|mot|1|mỗi|moi|per)?\s*"
    r"(tháng|thang|năm|nam|ngày|ngay|tuần|tuan|month|year|day|week)",
    re.IGNORECASE,
)
_PERIODS_PER_YEAR: Final = {
    "tháng": 12,
    "thang": 12,
    "month": 12,
    "năm": 1,
    "nam": 1,
    "year": 1,
    "ngày": 365,
    "ngay": 365,
    "day": 365,
    "tuần": 52,
    "tuan": 52,
    "week": 52,
}


def parse_stated_annual_rate(text: str | None) -> float | None:
    """ "1,2%/tháng" → 0.144 (phân số/năm, lãi đơn quy đổi). Chỉ để vạch mâu thuẫn (S2-EC13).

    Không khớp mẫu → ``None`` (không đoán). Giá trị >= 1000% coi như đọc sai → ``None``.
    """
    if not text:
        return None
    match = _RATE_RE.search(text)
    if match is None:
        return None
    try:
        pct = float(match.group(1).replace(",", "."))
    except ValueError:
        return None
    rate = pct / 100 * _PERIODS_PER_YEAR[match.group(2).lower()]
    return rate if 0 <= rate < 10 else None


def is_personal_account_holder(name: str | None) -> bool | None:
    """Tên chủ tài khoản không có dấu hiệu pháp nhân → tài khoản cá nhân (Chặng 4, File 2 §1.3).

    ``None`` khi không có tên (không đủ dữ kiện)."""
    if name is None or not name.strip():
        return None
    return _COMPANY_MARKERS.search(name) is None


@dataclass(frozen=True, slots=True)
class RegimeFact:
    period_count: int
    period_interval_days: int
    amount_per_period_vnd: int


@dataclass(frozen=True, slots=True)
class FeeFact:
    label: str
    amount_vnd: int
    uncertainty_max_vnd: int | None = None
    mandatory: bool = True


@dataclass(frozen=True, slots=True)
class LoanFacts:
    # --- bên cho vay tuyên bố ---
    claimed_lender_name: str | None = None
    claimed_company_legal_name: str | None = None
    # --- số liệu ---
    nominal_principal_vnd: int | None = None
    net_disbursed_vnd: int | None = None
    total_repayment_vnd: int | None = None
    tenure_days: int | None = None
    regimes: tuple[RegimeFact, ...] = ()
    fees: tuple[FeeFact, ...] = ()
    # --- cờ cảnh báo (chỉ nâng, không hạ) ---
    upfront_demanded: bool = False
    upfront_amount_vnd: int | None = None
    upfront_reason: str | None = None
    fake_frozen_receipt: bool = False
    morphology: SpecialMorphology = SpecialMorphology.NONE
    is_pawn_asset_secured: bool = False
    missing_critical_page: bool = False
    distinct_brands: int = 1
    stated_annual_rate: float | None = None
    # --- nguồn gốc tất định (server tạo, không từ LLM) ---
    apk_signature_ok: bool | None = None
    package_name: str | None = None
    beneficiary_is_personal: bool | None = None
    frozen_notice: bool = False
    # --- trạng thái xác nhận ---
    ambiguous: bool = False
    confidence: float = 1.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------ JSON ---
    def to_json(self) -> dict[str, Any]:
        """JSON đã che PII, sâu ≤ 4 cấp, gọn (< 64KB; EC-DB18). Không có STK/tên chủ TK."""
        return {
            "v": FACTS_VERSION,
            "lender": {
                "name": mask_optional(self.claimed_lender_name, 120),
                "legal": mask_optional(self.claimed_company_legal_name, 255),
            },
            "terms": {
                "p": self.nominal_principal_vnd,
                "n": self.net_disbursed_vnd,
                "t": self.total_repayment_vnd,
                "d": self.tenure_days,
                "rate": self.stated_annual_rate,
                "regimes": [
                    [r.period_count, r.period_interval_days, r.amount_per_period_vnd]
                    for r in self.regimes[:MAX_REGIMES]
                ],
                "fees": [
                    {
                        "l": mask_optional(f.label, 120),
                        "a": f.amount_vnd,
                        "m": f.uncertainty_max_vnd,
                        "c": f.mandatory,
                    }
                    for f in self.fees[:MAX_FEES]
                ],
            },
            "flags": {
                "upfront": self.upfront_demanded,
                "upfront_vnd": self.upfront_amount_vnd,
                "upfront_why": mask_optional(self.upfront_reason, 200),
                "frozen_receipt": self.fake_frozen_receipt,
                "morph": self.morphology.value,
                "pawn": self.is_pawn_asset_secured,
                "missing_page": self.missing_critical_page,
                "brands": self.distinct_brands,
                "ambiguous": self.ambiguous,
                "confidence": self.confidence,
            },
            "prov": {
                "apk_ok": self.apk_signature_ok,
                "pkg": self.package_name,
                "personal": self.beneficiary_is_personal,
                "frozen_notice": self.frozen_notice,
            },
            "notes": [mask_optional(n, 200) or "" for n in self.notes[:MAX_NOTES]],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> LoanFacts:
        terms = data.get("terms", {})
        lender = data.get("lender", {})
        flags = data.get("flags", {})
        prov = data.get("prov", {})
        return cls(
            claimed_lender_name=lender.get("name"),
            claimed_company_legal_name=lender.get("legal"),
            nominal_principal_vnd=terms.get("p"),
            net_disbursed_vnd=terms.get("n"),
            total_repayment_vnd=terms.get("t"),
            tenure_days=terms.get("d"),
            stated_annual_rate=terms.get("rate"),
            regimes=tuple(
                RegimeFact(int(a), int(b), int(c)) for a, b, c in terms.get("regimes", [])
            ),
            fees=tuple(
                FeeFact(str(f["l"] or ""), int(f["a"]), f.get("m"), bool(f.get("c", True)))
                for f in terms.get("fees", [])
            ),
            upfront_demanded=bool(flags.get("upfront", False)),
            upfront_amount_vnd=flags.get("upfront_vnd"),
            upfront_reason=flags.get("upfront_why"),
            fake_frozen_receipt=bool(flags.get("frozen_receipt", False)),
            morphology=SpecialMorphology(flags.get("morph", "NONE")),
            is_pawn_asset_secured=bool(flags.get("pawn", False)),
            missing_critical_page=bool(flags.get("missing_page", False)),
            distinct_brands=int(flags.get("brands", 1)),
            ambiguous=bool(flags.get("ambiguous", False)),
            confidence=float(flags.get("confidence", 1.0)),
            apk_signature_ok=prov.get("apk_ok"),
            package_name=prov.get("pkg"),
            beneficiary_is_personal=prov.get("personal"),
            frozen_notice=bool(prov.get("frozen_notice", False)),
            notes=tuple(str(n) for n in data.get("notes", [])),
        )

    # ------------------------------------------------------------ user edits ---
    def with_user_numbers(
        self,
        *,
        nominal_principal_vnd: int | None,
        net_disbursed_vnd: int,
        total_repayment_vnd: int | None,
        tenure_days: int | None,
        regimes: Sequence[RegimeFact],
        deducted_fees_vnd: int,
        upfront_fee_amount_vnd: int,
    ) -> LoanFacts:
        """Người dùng đã tự xác nhận con số → hết mơ hồ. Cờ cảnh báo giữ nguyên hoặc được nâng.

        ``deducted_fees_vnd > 0`` thay danh sách phí bằng 1 khoản gộp; ``== 0`` giữ phí đã đọc
        (không cho xóa phí bằng cách gửi 0). ``upfront_fee_amount_vnd > 0`` bật cờ phí ứng trước.
        """
        fees = self.fees
        if deducted_fees_vnd > 0:
            fees = (FeeFact("Phí khấu trừ (người dùng nhập)", deducted_fees_vnd, None, True),)
        demanded = self.upfront_demanded or upfront_fee_amount_vnd > 0
        amount = upfront_fee_amount_vnd if upfront_fee_amount_vnd > 0 else self.upfront_amount_vnd
        return replace(
            self,
            nominal_principal_vnd=nominal_principal_vnd,
            net_disbursed_vnd=net_disbursed_vnd,
            total_repayment_vnd=total_repayment_vnd,
            tenure_days=tenure_days,
            regimes=tuple(regimes),
            fees=fees,
            upfront_demanded=demanded,
            upfront_amount_vnd=amount,
            # Người dùng đã nhập đủ số → không còn "chờ xác nhận"; thiếu trang cũng được bù.
            ambiguous=False,
            missing_critical_page=False,
            confidence=1.0,
        )


def facts_from_extraction(
    extraction: MultiImageLoanExtraction,
    *,
    ambiguous: bool,
    apk_signature_ok: bool | None = None,
    package_name: str | None = None,
    distinct_brands: int | None = None,
) -> LoanFacts:
    """Ánh xạ schema LLM → ``LoanFacts`` (bảng ánh xạ ở ``app.verdict.inputs``)."""
    up = extraction.upfront_fee_demand
    cats = set(extraction.screen_categories)
    brands = (
        distinct_brands
        if distinct_brands is not None
        else len(extraction.distinct_loan_brands_detected)
    )
    return LoanFacts(
        claimed_lender_name=extraction.claimed_lender_name,
        claimed_company_legal_name=extraction.claimed_company_legal_name,
        nominal_principal_vnd=extraction.nominal_principal_vnd,
        net_disbursed_vnd=extraction.net_disbursed_vnd,
        total_repayment_vnd=extraction.total_repayment_stated_vnd,
        tenure_days=extraction.tenure_days_total,
        regimes=tuple(
            RegimeFact(r.period_count, r.period_interval_days, r.amount_per_period_vnd)
            for r in extraction.repayment_regimes
        ),
        fees=tuple(
            FeeFact(f.fee_label, f.amount_vnd, f.uncertainty_max_vnd, f.is_mandatory_or_prechecked)
            for f in extraction.deducted_fees_at_source
        ),
        upfront_demanded=up.demanded,
        upfront_amount_vnd=up.amount_vnd,
        upfront_reason=up.fee_name_claimed,
        fake_frozen_receipt=up.fake_frozen_receipt_claimed,
        morphology=SpecialMorphology(extraction.special_morphology.value),
        is_pawn_asset_secured=extraction.is_pawn_asset_secured,
        missing_critical_page=extraction.missing_critical_page_suspected,
        distinct_brands=max(1, brands),
        stated_annual_rate=parse_stated_annual_rate(extraction.stated_interest_rate_text),
        apk_signature_ok=apk_signature_ok,
        package_name=package_name,
        beneficiary_is_personal=is_personal_account_holder(up.beneficiary_account_holder),
        frozen_notice=ScreenCategory.SUSPICIOUS_NOTICE_RECEIPT in cats,
        ambiguous=ambiguous,
        confidence=extraction.critical_fields_min_confidence,
        notes=tuple(extraction.extraction_notes_vi),
    )
