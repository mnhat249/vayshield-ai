"""Mã phán quyết dùng chung toàn hệ thống (File 2 §4.4 + §4.1).

Đây là nguồn duy nhất cho 9 nhãn ``V_*`` và tín hiệu kernel
``INFINITE_APR_ADVANCE_FEE_SCAM``. Contract (`contracts/openapi.yaml`) sinh enum từ đây.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final


class VerdictCode(StrEnum):
    """9 nhãn phán quyết tất định (File 2 §4.4). Thứ tự = thứ tự trong đặc tả."""

    V_RED_ADVANCE_FEE = "V_RED_ADVANCE_FEE"
    V_RED_BRAND_SPOOF = "V_RED_BRAND_SPOOF"
    V_RED_BNPL_OR_STRAW_SCAM = "V_RED_BNPL_OR_STRAW_SCAM"
    V_RED_CRIMINAL_USURY = "V_RED_CRIMINAL_USURY"
    V_ORANGE_CIVIL_CAP_EXCEEDED = "V_ORANGE_CIVIL_CAP_EXCEEDED"
    V_ORANGE_PAWN_HIGH_FEE = "V_ORANGE_PAWN_HIGH_FEE"
    V_YELLOW_HIGH_COST_LICENSED = "V_YELLOW_HIGH_COST_LICENSED"
    V_AMBER_INCOMPLETE_DOC = "V_AMBER_INCOMPLETE_DOC"
    V_GREEN_TRANSPARENT = "V_GREEN_TRANSPARENT"


class KernelSignal(StrEnum):
    """Tín hiệu đặc biệt của kernel tài chính (File 2 §4.1, bất biến phí ứng trước).

    Khi ``F_upfront > 0`` và ``P_disbursed == 0`` kernel dừng bộ giải lãi và trả tín hiệu này;
    APR/XIRR khi đó là ``null`` trong mọi payload.
    """

    INFINITE_APR_ADVANCE_FEE_SCAM = "INFINITE_APR_ADVANCE_FEE_SCAM"


class Severity(StrEnum):
    """Mức màu hiển thị của phán quyết (File 2 §4.4 cột "Nhãn màu")."""

    CRITICAL_RED = "CRITICAL_RED"  # ĐỎ KHẨN CẤP
    RED = "RED"  # ĐỎ
    DEEP_ORANGE = "DEEP_ORANGE"  # CAM ĐẬM
    ORANGE = "ORANGE"  # CAM
    YELLOW_ORANGE = "YELLOW_ORANGE"  # VÀNG CAM
    AMBER = "AMBER"  # VÀNG NHẮC XÁC NHẬN
    GREEN = "GREEN"  # XANH


VERDICT_SEVERITY: Final = MappingProxyType(
    {
        VerdictCode.V_RED_ADVANCE_FEE: Severity.CRITICAL_RED,
        VerdictCode.V_RED_BRAND_SPOOF: Severity.CRITICAL_RED,
        VerdictCode.V_RED_BNPL_OR_STRAW_SCAM: Severity.CRITICAL_RED,
        VerdictCode.V_RED_CRIMINAL_USURY: Severity.RED,
        VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED: Severity.DEEP_ORANGE,
        VerdictCode.V_ORANGE_PAWN_HIGH_FEE: Severity.ORANGE,
        VerdictCode.V_YELLOW_HIGH_COST_LICENSED: Severity.YELLOW_ORANGE,
        VerdictCode.V_AMBER_INCOMPLETE_DOC: Severity.AMBER,
        VerdictCode.V_GREEN_TRANSPARENT: Severity.GREEN,
    }
)

VERDICT_LABEL_VI: Final = MappingProxyType(
    {
        VerdictCode.V_RED_ADVANCE_FEE: "ĐỎ KHẨN CẤP — Lừa đảo thu phí trước giải ngân",
        VerdictCode.V_RED_BRAND_SPOOF: "ĐỎ KHẨN CẤP — Mạo danh tổ chức tín dụng",
        VerdictCode.V_RED_BNPL_OR_STRAW_SCAM: "ĐỎ KHẨN CẤP — Bẫy rút hạn mức / đứng tên vay hộ",
        VerdictCode.V_RED_CRIMINAL_USURY: "ĐỎ — Dấu hiệu tín dụng đen (Điều 201 BLHS)",
        VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED: "CAM ĐẬM — Vượt trần dân sự 20%/năm",
        VerdictCode.V_ORANGE_PAWN_HIGH_FEE: "CAM — Vay cầm cố tài sản phí cao",
        VerdictCode.V_YELLOW_HIGH_COST_LICENSED: "VÀNG CAM — Hợp pháp nhưng chi phí cao",
        VerdictCode.V_AMBER_INCOMPLETE_DOC: "VÀNG — Thiếu trang hợp đồng, cần xác nhận",
        VerdictCode.V_GREEN_TRANSPARENT: "XANH — Hợp pháp & minh bạch",
    }
)

# Các nhãn mà APR bị ẩn khỏi UI (File 2 §4.4: V_RED_ADVANCE_FEE "Ẩn chỉ số APR").
APR_HIDDEN_VERDICTS: Final = frozenset({VerdictCode.V_RED_ADVANCE_FEE})


def severity_of(code: VerdictCode) -> Severity:
    """Trả mức màu của một mã phán quyết."""
    return VERDICT_SEVERITY[code]


def is_red(code: VerdictCode) -> bool:
    """True nếu phán quyết thuộc nhóm ĐỎ (cần chặn ngay)."""
    return VERDICT_SEVERITY[code] in (Severity.CRITICAL_RED, Severity.RED)
