"""Khoảng nhạy APR_VN-365 ``[APR_min, APR_max]`` cho tầng phán quyết (File 2 §1.3 chặng 5, S2-EC41).

Mọi lãi suất là **phân số/năm** như kernel T02 (1.00 = 100%/năm).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.verdict_codes import KernelSignal
from app.finance.solvers import BRACKET_HI, RateReport

__all__ = ["AprInterval", "effective_apr"]


def effective_apr(report: RateReport) -> float | None:
    """APR dùng để so mốc pháp lý theo quy ước ``RateReport`` của T02.

    ``apr_primary`` → nếu ``None``: lãi vượt cận trên khoảng kẹp ⇒ ``BRACKET_HI`` (cận dưới an
    toàn); đa nghiệm không có nghiệm ``>= 0`` ⇒ ``mirr``; còn lại ``None`` (không xác định).
    """
    if report.apr_primary is not None:
        return report.apr_primary
    if report.apr_above_bracket:
        return BRACKET_HI
    if report.multiple_roots and report.mirr is not None:
        return report.mirr
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class AprInterval:
    """Kết quả kernel cho 1 khoản vay. ``apr_min is None`` ⇔ không xác định được APR."""

    apr_min: float | None = None
    apr_max: float | None = None
    apr_point: float | None = None
    ear_xirr: float | None = None
    kernel_signal: KernelSignal | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        lo, hi = self.apr_min, self.apr_max
        if (lo is None) != (hi is None):
            raise ValueError("apr_min and apr_max must both be set or both be None")
        if lo is not None and hi is not None and (math.isnan(lo) or math.isnan(hi) or lo > hi):
            raise ValueError("invalid APR interval")

    @property
    def known(self) -> bool:
        return self.apr_min is not None

    @classmethod
    def from_bounds(cls, apr_min: float, apr_max: float) -> AprInterval:
        point = apr_min if apr_min == apr_max else None
        return cls(apr_min=apr_min, apr_max=apr_max, apr_point=point)

    @classmethod
    def from_report(cls, report: RateReport) -> AprInterval:
        return cls.from_reports((report,))

    @classmethod
    def from_reports(cls, reports: Sequence[RateReport]) -> AprInterval:
        """Gộp các kịch bản OCR (best/worst...). Một kịch bản không xác định ⇒ cả khoảng
        không xác định (an toàn: không kết luận trên dữ liệu thiếu)."""
        if not reports:
            return cls.unavailable("NO_SCENARIO")
        known: list[float] = []
        for report in reports:
            value = effective_apr(report)
            if value is None:
                return cls.unavailable(report.apr_unavailable_reason or "APR_UNAVAILABLE")
            known.append(value)
        lo, hi = min(known), max(known)
        single = len(reports) == 1
        return cls(
            apr_min=lo,
            apr_max=hi,
            apr_point=lo if single or lo == hi else None,
            ear_xirr=reports[0].ear_xirr if single else None,
        )

    @classmethod
    def unavailable(cls, reason: str) -> AprInterval:
        return cls(unavailable_reason=reason)

    @classmethod
    def advance_fee(cls) -> AprInterval:
        """Kernel đã dừng bộ giải vì phí trước & chưa giải ngân (``AdvanceFeeScamError``)."""
        return cls(
            kernel_signal=KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM,
            unavailable_reason=KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM.value,
        )

    def raised_to(self, floor: float) -> AprInterval:
        """Nâng cả 2 cận lên ít nhất ``floor`` (chi phí cộng thêm chỉ làm APR tăng)."""
        if self.apr_min is None or self.apr_max is None:
            return AprInterval(
                apr_min=floor, apr_max=floor, apr_point=floor, kernel_signal=self.kernel_signal
            )
        lo, hi = max(self.apr_min, floor), max(self.apr_max, floor)
        unchanged = lo == self.apr_min and hi == self.apr_max
        return AprInterval(
            apr_min=lo,
            apr_max=hi,
            apr_point=self.apr_point if unchanged else (lo if lo == hi else None),
            ear_xirr=self.ear_xirr if unchanged else None,
            kernel_signal=self.kernel_signal,
        )
