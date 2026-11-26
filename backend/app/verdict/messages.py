"""Lời giải thích & kịch bản giọng nói tiếng Việt đời thường cho phán quyết.

Không chứa nguyên văn điều luật hay số điều — căn cứ pháp lý chỉ đi qua ``legal_basis_ids``
(T13 giải thích). Không chứa PII (STK, tên chủ tài khoản, SĐT người vay).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from app.core.verdict_codes import VerdictCode

__all__ = ["Contradiction", "fmt_pct", "fmt_vnd", "reason_text", "tts_script"]


class Contradiction(StrEnum):
    """Mã mâu thuẫn / cờ phát hiện (điền ``contradictions`` của contract)."""

    ADVANCE_FEE_DEMANDED = "ADVANCE_FEE_DEMANDED"
    LOOKALIKE_BRAND = "LOOKALIKE_BRAND"
    BRAND_ENTITY_MISMATCH = "BRAND_ENTITY_MISMATCH"
    APK_SIGNATURE_MISMATCH = "APK_SIGNATURE_MISMATCH"
    PERSONAL_BENEFICIARY_ACCOUNT = "PERSONAL_BENEFICIARY_ACCOUNT"
    FAKE_FROZEN_ACCOUNT_NOTICE = "FAKE_FROZEN_ACCOUNT_NOTICE"
    LICENSED_BRAND_USURIOUS_RATE = "LICENSED_BRAND_USURIOUS_RATE"
    BNPL_ILLEGAL_CASHOUT = "BNPL_ILLEGAL_CASHOUT"
    STRAW_BORROWER_RECRUITMENT = "STRAW_BORROWER_RECRUITMENT"
    COMMODITY_RESALE_USURY = "COMMODITY_RESALE_USURY"
    SCAM_RECOVERY_FRAUD = "SCAM_RECOVERY_FRAUD"
    STATED_RATE_CONTRADICTION = "STATED_RATE_CONTRADICTION"
    LOAN_EXTENSION_FEE = "LOAN_EXTENSION_FEE"
    P2P_SANDBOX_CAP_EXCEEDED = "P2P_SANDBOX_CAP_EXCEEDED"
    INTERVAL_CROSSES_LEGAL_THRESHOLD = "INTERVAL_CROSSES_LEGAL_THRESHOLD"
    APR_UNAVAILABLE = "APR_UNAVAILABLE"
    MISSING_CRITICAL_PAGE = "MISSING_CRITICAL_PAGE"
    MULTIPLE_LOANS_MIXED = "MULTIPLE_LOANS_MIXED"
    LENDER_NOT_VERIFIED = "LENDER_NOT_VERIFIED"
    BUNDLED_INSURANCE = "BUNDLED_INSURANCE"


def fmt_pct(rate: float) -> str:
    """0.9653 → ``96,5%`` (phân số/năm → %, dấu phẩy thập phân kiểu Việt)."""
    return f"{rate * 100:.1f}".replace(".", ",") + "%"


def fmt_vnd(amount: int) -> str:
    return f"{amount:,}".replace(",", ".") + "đ"


def fmt_days(days: Decimal) -> str:
    return f"{days:.1f}".replace(".", ",")


_REASONS: Final = MappingProxyType(
    {
        Contradiction.ADVANCE_FEE_DEMANDED: (
            "Bên kia đòi chuyển tiền trước khi giải ngân. Nơi cho vay thật không bao giờ bắt "
            "nộp tiền trước."
        ),
        Contradiction.LOOKALIKE_BRAND: (
            "Tên hoặc địa chỉ web gần giống {brand} nhưng không trùng tên hay tên miền chính thức."
        ),
        Contradiction.BRAND_ENTITY_MISMATCH: (
            "Tên thương hiệu và tên công ty trên giấy tờ thuộc hai tổ chức khác nhau."
        ),
        Contradiction.APK_SIGNATURE_MISMATCH: (
            "Ứng dụng trên máy không phải bản chính thức của {brand}."
        ),
        Contradiction.PERSONAL_BENEFICIARY_ACCOUNT: (
            "Tiền bị yêu cầu chuyển vào tài khoản cá nhân, không phải tài khoản công ty."
        ),
        Contradiction.FAKE_FROZEN_ACCOUNT_NOTICE: (
            "Có thông báo 'tài khoản bị treo, đóng băng' để ép nộp thêm tiền."
        ),
        Contradiction.LICENSED_BRAND_USURIOUS_RATE: (
            "Lãi thật từ 100%/năm trở lên, không khớp cách cho vay của {brand}."
        ),
        Contradiction.BNPL_ILLEGAL_CASHOUT: (
            "Bị gạ quét mã QR để rút tiền mặt từ ví trả sau, dễ mất trắng hạn mức."
        ),
        Contradiction.STRAW_BORROWER_RECRUITMENT: (
            "Bị rủ đứng tên vay hộ lấy hoa hồng, món nợ thật sẽ mang tên mình."
        ),
        Contradiction.COMMODITY_RESALE_USURY: (
            "Mua hàng trả góp rồi bán lại ngay tại quầy giá thấp, thực chất là vay lãi cao."
        ),
        Contradiction.SCAM_RECOVERY_FRAUD: (
            "Dịch vụ hứa lấy lại tiền hoặc xóa nợ xấu nhưng đòi đóng phí trước."
        ),
        Contradiction.STATED_RATE_CONTRADICTION: (
            "Lãi ghi trên quảng cáo ({stated}/năm) khác xa lãi thật tính từ số tiền phải trả "
            "({actual}/năm)."
        ),
        Contradiction.LOAN_EXTENSION_FEE: (
            "Phí gia hạn {fee} cho {days} ngày không làm giảm nợ gốc, tương đương {apr}/năm."
        ),
        Contradiction.P2P_SANDBOX_CAP_EXCEEDED: (
            "Số tiền chào vay {principal} vượt hạn mức {cap} của nền tảng P2P thử nghiệm."
        ),
        Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD: (
            "Ảnh chưa rõ nên lãi thật nằm trong khoảng {lo} đến {hi}/năm, chạm ngưỡng pháp lý."
        ),
        Contradiction.APR_UNAVAILABLE: "Chưa đủ số liệu (kỳ hạn, lịch trả) để tính lãi thật.",
        Contradiction.MISSING_CRITICAL_PAGE: (
            "Có vẻ còn thiếu trang hợp đồng ghi kỳ hạn hoặc lịch trả."
        ),
        Contradiction.MULTIPLE_LOANS_MIXED: (
            "Các ảnh có vẻ thuộc nhiều khoản vay khác nhau, cần kiểm tra từng khoản."
        ),
        Contradiction.LENDER_NOT_VERIFIED: (
            "Không thấy bên cho vay trong danh sách tổ chức được cấp phép."
        ),
        Contradiction.BUNDLED_INSURANCE: (
            "Có phí bảo hiểm tính kèm khoản vay; người vay có quyền từ chối bảo hiểm không bắt "
            "buộc."
        ),
    }
)

_VERDICT_REASON: Final = MappingProxyType(
    {
        VerdictCode.V_RED_ADVANCE_FEE: "Đây là kiểu lừa thu phí trước rồi không giải ngân.",
        VerdictCode.V_RED_BRAND_SPOOF: "Có dấu hiệu mạo danh {brand}.",
        VerdictCode.V_RED_BNPL_OR_STRAW_SCAM: ("Đây là bẫy chiếm hạn mức hoặc bắt đứng tên nợ hộ."),
        VerdictCode.V_RED_CRIMINAL_USURY: (
            "Ngay cả mức thấp nhất, lãi thật cũng từ {lo}/năm, gấp nhiều lần mức pháp luật cho "
            "phép."
        ),
        VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED: (
            "Lãi thật khoảng {apr}/năm, vượt mức 20%/năm; phần vượt không được pháp luật bảo vệ."
        ),
        VerdictCode.V_ORANGE_PAWN_HIGH_FEE: (
            "Tính cả phí thẩm định và giữ tài sản, chi phí thật khoảng {apr}/năm."
        ),
        VerdictCode.V_YELLOW_HIGH_COST_LICENSED: (
            "Tổ chức được cấp phép, nhưng tổng chi phí thật khoảng {apr}/năm là cao."
        ),
        VerdictCode.V_AMBER_INCOMPLETE_DOC: "Chưa đủ căn cứ để kết luận, cần xác nhận thêm.",
        VerdictCode.V_GREEN_TRANSPARENT: "Khoản vay minh bạch, lãi thật khoảng {apr}/năm.",
    }
)

_TTS: Final = MappingProxyType(
    {
        VerdictCode.V_RED_ADVANCE_FEE: (
            "Cô chú dừng lại ngay! Đây là lừa đảo thu phí trước. Nơi cho vay thật không bao giờ "
            "bắt chuyển tiền trước khi giải ngân. Tuyệt đối không chuyển thêm đồng nào."
        ),
        VerdictCode.V_RED_BRAND_SPOOF: (
            "Cô chú dừng lại ngay! Đây có dấu hiệu mạo danh {brand}. Đừng chuyển tiền và đừng "
            "đọc mã OTP cho ai."
        ),
        VerdictCode.V_RED_BNPL_OR_STRAW_SCAM: (
            "Cô chú dừng lại ngay! Đây là bẫy rút tiền ví trả sau hoặc rủ đứng tên vay hộ. Làm "
            "theo là có thể mất hết hạn mức và gánh nợ thật mang tên mình."
        ),
        VerdictCode.V_RED_CRIMINAL_USURY: (
            "Cảnh báo! Lãi thật của khoản vay này từ {lo} một năm trở lên, gấp nhiều lần mức "
            "pháp luật cho phép. Đây là dấu hiệu tín dụng đen. Cô chú đừng ký và đừng cho ứng "
            "dụng đọc danh bạ hay hình ảnh."
        ),
        VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED: (
            "Chú ý! Lãi thật khoảng {apr} một năm, vượt mức hai mươi phần trăm một năm. Phần "
            "vượt không được pháp luật bảo vệ. Cô chú cân nhắc kỹ trước khi ký."
        ),
        VerdictCode.V_ORANGE_PAWN_HIGH_FEE: (
            "Chú ý! Khoản cầm cố này tính cả phí thì chi phí thật khoảng {apr} một năm. Cô chú "
            "cân nhắc rủi ro mất xe hay tài sản đi làm."
        ),
        VerdictCode.V_YELLOW_HIGH_COST_LICENSED: (
            "Khoản vay này hợp pháp nhưng chi phí cao, khoảng {apr} một năm. Cô chú có quyền từ "
            "chối mua bảo hiểm không bắt buộc."
        ),
        VerdictCode.V_AMBER_INCOMPLETE_DOC: (
            "Chưa đủ thông tin để kết luận. Cô chú chụp thêm trang có kỳ hạn và lịch trả nợ, "
            "hoặc chọn số tiền trả mỗi kỳ để tính lại."
        ),
        VerdictCode.V_GREEN_TRANSPARENT: (
            "Khoản vay này minh bạch, lãi thật khoảng {apr} một năm. Sau khi nhận tiền, ai gọi "
            "điện bắt chuyển tiền đi thì đó là lừa đảo."
        ),
    }
)


class _SafeDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "?"


def reason_text(item: Contradiction | VerdictCode, **values: str) -> str:
    template = _REASONS[item] if isinstance(item, Contradiction) else _VERDICT_REASON[item]
    return template.format_map(_SafeDict(values))


def tts_script(
    code: VerdictCode,
    *,
    brand: str | None,
    hotline: str | None,
    apr: str | None,
    lo: str | None,
    lost_workdays: Decimal | None,
) -> str:
    values = _SafeDict(
        brand=brand or "một tổ chức tín dụng",
        apr=apr or "chưa rõ",
        lo=lo or "chưa rõ",
    )
    parts = [_TTS[code].format_map(values)]
    if (
        lost_workdays is not None
        and lost_workdays > 0
        and code is not VerdictCode.V_GREEN_TRANSPARENT
    ):
        subject = (
            "Khoản tiền bị đòi" if code is VerdictCode.V_RED_ADVANCE_FEE else "Tiền lãi và phí"
        )
        parts.append(f"{subject} bằng khoảng {fmt_days(lost_workdays)} ngày công lao động.")
    if hotline and code in (VerdictCode.V_RED_ADVANCE_FEE, VerdictCode.V_RED_BRAND_SPOOF):
        parts.append(
            f"Hãy tự gọi tổng đài chính thức {hotline} của {brand or 'tổ chức đó'} để đối chứng."
        )
    return " ".join(parts)
