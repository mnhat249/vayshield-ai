"""Kiểu đầu vào thuần dữ liệu của ma trận phán quyết (độc lập với ``ai_pipeline.extraction``).

Ánh xạ từ ``MultiImageLoanExtraction`` v2.0 (File 3 §2.2) — để T11 viết adapter:

======================================  ==========================================================
``MultiImageLoanExtraction``            ``VerdictInput``
======================================  ==========================================================
``claimed_lender_name``                 ``claimed_lender_name``
``claimed_company_legal_name``          ``claimed_company_legal_name``
(URL/domain đọc trên ảnh, nếu có)       ``claimed_domain``
``nominal_principal_vnd`` (float)       ``principal_vnd`` (int đồng, làm tròn ở adapter)
``net_disbursed_vnd`` (float)           ``net_disbursed_vnd`` (int đồng)
``is_pawn_asset_secured``               ``is_pawn_asset_secured``
``special_morphology``                  ``special_morphology``
``upfront_fee_demand.demanded``         ``upfront_fee_demand.demanded``
``upfront_fee_demand.amount_vnd``       ``upfront_fee_demand.amount_vnd`` (int đồng)
``upfront_fee_demand.fake_frozen_…``    ``upfront_fee_demand.fake_frozen_receipt_claimed``
``deducted_fees_at_source[]``           ``deducted_fees`` (``DeductedFee``; EC-AI24 dùng
                                        ``is_mandatory_or_prechecked``)
``stated_interest_rate_text``           ``stated_annual_rate`` — adapter tự PARSE tất định sang
                                        phân số/năm (vd "1,2%/tháng" → 0.144); chỉ dùng để phát
                                        hiện mâu thuẫn (S2-EC13), KHÔNG dùng tính APR.
``is_loan_extension_rollover`` + số     ``rollover`` (``RolloverTerms``, S2-EC15)
liệu phí gia hạn
``missing_critical_page_suspected``     ``missing_critical_page_suspected``
``len(distinct_loan_brands_detected)``  ``distinct_loan_brands_count``
(vector CF do T02 ``build_cashflows``)  ``cashflows`` (để tính chỉ số sinh kế)
======================================  ==========================================================

Các trường KHÔNG có ở đây một cách cố ý: mọi kết luận "hợp pháp/trái luật", lãi suất năm do LLM
tự tính, ``extraction_notes_vi``. LLM không có kênh nào để đặt hay hạ phán quyết. Loại pháp nhân
LLM "tuyên bố" (``claimed_lender_type``) chỉ để hiển thị — tư cách cấp phép CHỈ lấy từ
``LenderRegistry``.

``Provenance`` là kết quả tất định của chặng 2 (T03: SHA-256 APK, VietQR) — không từ LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.schemas.common import LegalCategory
from app.finance.cashflow import CashFlowPoint
from app.finance.livelihood import DEFAULT_DAILY_WAGE_VND

__all__ = [
    "DeductedFee",
    "Provenance",
    "RolloverTerms",
    "SpecialMorphology",
    "UpfrontFeeDemand",
    "VerdictInput",
    "VerdictInputError",
]


class VerdictInputError(ValueError):
    """Đầu vào vi phạm bất biến (tiền âm, kiểu sai...)."""


class SpecialMorphology(StrEnum):
    """Trùng giá trị với ``SpecialScamMorphology`` (File 3 §2.2)."""

    NONE = "NONE"
    BNPL_ILLEGAL_CASHOUT = "BNPL_ILLEGAL_CASHOUT"
    STRAW_BORROWER_RECRUITMENT = "STRAW_BORROWER_RECRUITMENT"
    COMMODITY_RESALE_USURY = "COMMODITY_RESALE_USURY"
    SCAM_RECOVERY_FRAUD = "SCAM_RECOVERY_FRAUD"


def _check_vnd(name: str, value: int | None, *, positive: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise VerdictInputError(f"{name} must be int VND")
    if value < 0 or (positive and value == 0):
        raise VerdictInputError(f"{name} out of range")


@dataclass(frozen=True, slots=True, kw_only=True)
class UpfrontFeeDemand:
    """Yêu cầu chuyển tiền túi TRƯỚC khi nhận giải ngân (Bẫy 1)."""

    demanded: bool = False
    amount_vnd: int | None = None
    fake_frozen_receipt_claimed: bool = False

    def __post_init__(self) -> None:
        _check_vnd("upfront_fee_demand.amount_vnd", self.amount_vnd)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeductedFee:
    """Phí khấu trừ tại nguồn (``DeductedFeeItem``). ``is_insurance=None`` → tự nhận từ nhãn."""

    label: str
    amount_vnd: int
    uncertainty_max_vnd: int | None = None
    is_mandatory_or_prechecked: bool = True
    is_insurance: bool | None = None

    def __post_init__(self) -> None:
        _check_vnd("deducted_fee.amount_vnd", self.amount_vnd)
        _check_vnd("deducted_fee.uncertainty_max_vnd", self.uncertainty_max_vnd)


@dataclass(frozen=True, slots=True, kw_only=True)
class RolloverTerms:
    """Phí "gia hạn giữ gốc" (S2-EC15): đóng ``extension_fee_vnd`` để lùi hạn
    ``extension_period_days`` ngày cho khoản gốc ``principal_vnd``."""

    principal_vnd: int
    extension_fee_vnd: int
    extension_period_days: int

    def __post_init__(self) -> None:
        _check_vnd("rollover.principal_vnd", self.principal_vnd, positive=True)
        _check_vnd("rollover.extension_fee_vnd", self.extension_fee_vnd)
        if self.extension_period_days <= 0:
            raise VerdictInputError("rollover.extension_period_days must be > 0")


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Kết quả xác minh nguồn gốc tất định (chặng 2, T03).

    - ``apk_signature_ok``: ``True`` = SHA-256 chứng chỉ APK khớp chữ ký chính thức của tổ chức;
      ``False`` = sai chữ ký; ``None`` = không kiểm (chỉ có ảnh chụp).
    - ``beneficiary_is_personal_account``: STK/QR nhận tiền là tài khoản cá nhân (VietQR).
    - ``account_frozen_notice``: ảnh báo "tài khoản bị treo/đóng băng trên hệ thống".
    """

    apk_signature_ok: bool | None = None
    package_name: str | None = None
    beneficiary_is_personal_account: bool | None = None
    account_frozen_notice: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class VerdictInput:
    """Dữ kiện khoản vay đã chuẩn hóa cho ``decide_verdict`` (xem bảng ánh xạ ở đầu module)."""

    claimed_lender_name: str | None = None
    claimed_company_legal_name: str | None = None
    claimed_domain: str | None = None
    claimed_lender_type: LegalCategory | None = None
    principal_vnd: int | None = None
    net_disbursed_vnd: int | None = None
    is_pawn_asset_secured: bool = False
    special_morphology: SpecialMorphology = SpecialMorphology.NONE
    upfront_fee_demand: UpfrontFeeDemand = field(default_factory=UpfrontFeeDemand)
    deducted_fees: tuple[DeductedFee, ...] = ()
    stated_annual_rate: float | None = None
    rollover: RolloverTerms | None = None
    missing_critical_page_suspected: bool = False
    distinct_loan_brands_count: int = 1
    cashflows: tuple[CashFlowPoint, ...] = ()
    daily_wage_vnd: int = DEFAULT_DAILY_WAGE_VND

    def __post_init__(self) -> None:
        _check_vnd("principal_vnd", self.principal_vnd, positive=True)
        _check_vnd("net_disbursed_vnd", self.net_disbursed_vnd)
        _check_vnd("daily_wage_vnd", self.daily_wage_vnd, positive=True)
        if self.stated_annual_rate is not None and self.stated_annual_rate < 0:
            raise VerdictInputError("stated_annual_rate must be >= 0")
        if self.distinct_loan_brands_count < 0:
            raise VerdictInputError("distinct_loan_brands_count must be >= 0")
