"""Schema trích xuất ``MultiImageLoanExtraction`` v2.0 (File 3 §2.2).

Tên lớp, tên field, giá trị enum, ràng buộc ``ge``/``gt``/``le`` và mô tả giữ đúng đặc tả.
Khác biệt có chủ đích duy nhất: field tiền ``*_vnd`` có kiểu ``int`` (đồng nguyên) thay vì
``float`` (CLAUDE.md §4: tiền không dùng float). Đầu ra JSON của LLM vẫn là số; số nguyên
dạng float (``10500000.0``) được nhận, số lẻ được làm tròn ``ROUND_HALF_UP`` về đồng.

LLM KHÔNG tính lãi suất năm và KHÔNG kết luận hợp pháp/trái luật trong schema này.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field

SCHEMA_VERSION = "2.0"


def _to_vnd_int(value: Any) -> Any:
    """Chuẩn hóa số tiền về int đồng; để Pydantic báo lỗi với kiểu không hợp lệ."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("số tiền phải hữu hạn")
        return int(Decimal(repr(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("số tiền phải hữu hạn")
        return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation:
            return value
        if not parsed.is_finite():
            raise ValueError("số tiền phải hữu hạn")
        return int(parsed.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return value


VndAmount = Annotated[int, BeforeValidator(_to_vnd_int)]


class ScreenCategory(StrEnum):
    MARKETING_OFFER = "MARKETING_OFFER"  # Màn hình quảng cáo hạn mức đầu tiên
    CONTRACT_TERMS = "CONTRACT_TERMS"  # Màn hình điều khoản / hợp đồng chi tiết
    DISBURSEMENT_SUMMARY = "DISBURSEMENT_SUMMARY"  # Màn hình xác nhận thực nhận & lịch trả
    CHAT_CONVERSATION = "CHAT_CONVERSATION"  # Tin nhắn Zalo/SMS/Messenger với "nhân viên"
    SUSPICIOUS_NOTICE_RECEIPT = "SUSPICIOUS_RECEIPT"  # Ảnh báo lỗi sai STK, đóng băng, bill treo
    VOICE_DICTATED_TERMS = "VOICE_DICTATED_TERMS"  # Điều khoản đọc miệng (FLAG_SECURE chặn chụp)
    NON_LOAN_IRRELEVANT = "NON_LOAN_IRRELEVANT"  # Ảnh không liên quan tài chính/vay vốn


class SpecialScamMorphology(StrEnum):
    NONE = "NONE"
    BNPL_ILLEGAL_CASHOUT = "BNPL_ILLEGAL_CASHOUT"  # Gạ quét QR rút tiền mặt Ví trả sau / Thẻ TD
    STRAW_BORROWER_RECRUITMENT = "STRAW_BORROWER_RECRUITMENT"  # Dụ đứng tên ký vay hộ
    COMMODITY_RESALE_USURY = "COMMODITY_RESALE_USURY"  # Mua trả góp điện thoại bán lại cắt phế
    SCAM_RECOVERY_FRAUD = "SCAM_RECOVERY_FRAUD"  # Dịch vụ giả "thu hồi tiền treo / xóa nợ xấu"


class UpfrontFeeDemand(BaseModel):
    demanded: bool = Field(
        description="True nếu có bất kỳ yêu cầu chuyển tiền/nộp phí nào TRƯỚC khi nhận giải ngân"
    )
    fee_name_claimed: str | None = Field(
        default=None,
        description="Tên khoản tiền bị đòi: phí hồ sơ, bảo hiểm, mở băng, đối ứng, thuế...",
    )
    amount_vnd: VndAmount | None = Field(default=None, ge=0)
    beneficiary_account_number: str | None = None
    beneficiary_account_holder: str | None = None
    beneficiary_bank_name: str | None = None
    is_sender_incoming_message: bool = Field(
        default=True,
        description="True nếu STK/QR do phía bên kia gửi tới, False nếu là của chính người vay",
    )
    fake_frozen_receipt_claimed: bool = Field(
        default=False,
        description="True nếu gửi ảnh bill báo tiền đang treo chờ nộp phí giải tỏa",
    )
    coercion_reason: str | None = Field(default=None)
    verbatim_evidence: str | None = Field(default=None)


class RepaymentRegimeSegment(BaseModel):
    period_count: int = Field(ge=1, le=360, description="Số kỳ trong giai đoạn này")
    period_interval_days: int = Field(
        ge=1, le=365, description="Số ngày mỗi kỳ (VD: 1, 7, 14, 30 ngày)"
    )
    amount_per_period_vnd: VndAmount = Field(
        ge=0, description="Số tiền phải đóng mỗi kỳ trong giai đoạn này"
    )


class DeductedFeeItem(BaseModel):
    fee_label: str
    amount_vnd: VndAmount = Field(ge=0)
    uncertainty_max_vnd: VndAmount | None = Field(
        default=None, ge=0, description="Cận trên nếu chữ số bị lóa/mờ nhẹ"
    )
    is_mandatory_or_prechecked: bool = True


class MultiImageLoanExtraction(BaseModel):
    screen_categories: list[ScreenCategory]
    distinct_loan_brands_detected: list[str] = Field(
        default_factory=list,
        description="Danh sách các app/tổ chức khác nhau nếu user chọn lẫn ảnh 2 khoản vay",
    )
    claimed_lender_name: str | None = None
    claimed_company_legal_name: str | None = None
    is_pawn_asset_secured: bool = Field(
        default=False, description="True nếu là hợp đồng cầm cố tài sản/đăng ký xe thực tế"
    )
    is_loan_extension_rollover: bool = Field(default=False)
    special_morphology: SpecialScamMorphology = SpecialScamMorphology.NONE
    nominal_principal_vnd: VndAmount | None = Field(default=None, gt=0)
    net_disbursed_vnd: VndAmount | None = Field(
        default=None,
        ge=0,
        description="Tiền thực nhận (hoặc tiền bán lại máy cầm về trong Commodity Usury)",
    )
    total_repayment_stated_vnd: VndAmount | None = Field(default=None, gt=0)
    tenure_days_total: int | None = Field(default=None, ge=1, le=3650)
    stated_interest_rate_text: str | None = None
    deducted_fees_at_source: list[DeductedFeeItem] = Field(default_factory=list)
    repayment_regimes: list[RepaymentRegimeSegment] = Field(
        default_factory=list,
        description="Hỗ trợ cả trả góp đều 1 chế độ và lãi 2 giai đoạn / ân hạn gốc",
    )
    late_penalty_text: str | None = None
    upfront_fee_demand: UpfrontFeeDemand
    bait_and_switch_detected: bool = Field(default=False)
    missing_critical_page_suspected: bool = Field(
        default=False, description="True nếu hợp đồng bị cắt ngang thiếu kỳ hạn/lịch trả"
    )
    critical_fields_min_confidence: float = Field(ge=0.0, le=1.0)
    extraction_notes_vi: list[str] = Field(default_factory=list)


MONEY_FIELDS: tuple[str, ...] = (
    "nominal_principal_vnd",
    "net_disbursed_vnd",
    "total_repayment_stated_vnd",
)


def is_non_loan_only(extraction: MultiImageLoanExtraction) -> bool:
    """EC-AI10: mọi ảnh đều ``NON_LOAN_IRRELEVANT`` (hoặc không phân loại được)."""
    cats = set(extraction.screen_categories)
    return not cats or cats == {ScreenCategory.NON_LOAN_IRRELEVANT}
