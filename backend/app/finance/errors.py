"""Lỗi tất định của kernel tài chính (File 2 §4.1, §4.2).

Mỗi lỗi mang ``code`` ổn định để tầng phán quyết (T09) / API (T11) ánh xạ, không dựa vào chuỗi
thông điệp. Không lỗi nào chứa PII — chỉ mã và số liệu dòng tiền.
"""

from __future__ import annotations

from typing import Final

from app.core.verdict_codes import KernelSignal

INVALID_OR_ADVANCE_FEE_CASHFLOW: Final = "INVALID_OR_ADVANCE_FEE_CASHFLOW"
MISSING_REPAYMENT_OUTFLOWS: Final = "MISSING_REPAYMENT_OUTFLOWS"
NON_MONOTONIC_OR_EXTREME_CASHFLOW: Final = "NON_MONOTONIC_OR_EXTREME_CASHFLOW"
MULTIPLE_IRR_ROOTS: Final = "MULTIPLE_IRR_ROOTS"
INVALID_INPUT: Final = "INVALID_INPUT"


class FinanceError(ValueError):
    """Lỗi chung của kernel. ``code`` là mã máy đọc được."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class AdvanceFeeScamError(FinanceError):
    """Bất biến Bẫy 1: đòi phí từ túi người vay khi chưa giải ngân → dừng bộ giải lãi."""

    def __init__(self) -> None:
        super().__init__(KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM.value)
        self.signal = KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM


class RateOutOfBracketError(FinanceError):
    """Nghiệm nằm ngoài khoảng kẹp ``[-0.99, 500]`` (vd XIRR của khoản vay 1 ngày gấp đôi).

    ``direction`` = ``"above"`` nghĩa là lãi suất > cận trên (≥ 50.000%/năm) — vẫn là kết luận
    an toàn cho phán quyết cận dưới; ``"below"`` nghĩa là dòng tiền suy biến.
    """

    def __init__(self, direction: str, bound: float) -> None:
        super().__init__(NON_MONOTONIC_OR_EXTREME_CASHFLOW, f"rate {direction} bracket {bound}")
        self.direction = direction
        self.bound = bound


class MultipleRootsError(FinanceError):
    """Dòng tiền đổi dấu nhiều lần sinh > 1 nghiệm IRR trong khoảng kẹp (S2-EC40)."""

    def __init__(self, roots: tuple[float, ...]) -> None:
        super().__init__(MULTIPLE_IRR_ROOTS, f"{len(roots)} roots")
        self.roots = roots
