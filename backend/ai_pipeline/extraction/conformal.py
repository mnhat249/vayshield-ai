"""Cổng leo thang bất định hiệu chỉnh (``Split Conformal Uncertainty Escalation Gate``).

File 3 §1.2: leo thang Flash → Pro (crop ROI) khi BẤT KỲ điều kiện nào đúng:
``max H_tok > τ_H`` ∨ ``min c_f < 0.82`` ∨ ``|TotalStated − Σ|CF_k|| > 2%·P_nominal`` ∨
``BrandConflictFlag``. Không tin điểm tự chấm của LLM (EC-AI15): độ tin cậy hiệu chỉnh là min
của điểm tự chấm và xác suất top-1 chữ số; top-1/top-2 sát nhau → hạ xuống < 0.55.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from .schemas import MultiImageLoanExtraction, ScreenCategory, is_non_loan_only
from .uncertainty import DigitUncertainty, critical_uncertainties

CONFIDENCE_FLOOR = 0.82  # File 3 §1.2
TOTAL_MISMATCH_PERCENT = 2  # File 3 §1.2: > 2% gốc danh nghĩa
ENTROPY_THRESHOLD_BITS = 0.9  # τ_H — giả định (đặc tả không cho số), có thể hiệu chỉnh
DIGIT_MARGIN_MIN = 0.2  # top1 − top2 < 0.2 coi là "sát nhau" (VD 0.52 vs 0.44)
AMBIGUOUS_DIGIT_CONFIDENCE = 0.5  # EC-AI15: hạ xuống < 0.55
USER_CONFIRM_BELOW = 0.55  # EC-AI15: ép người dùng xác nhận tay
IMAGE_QUALITY_FLOOR = 0.5  # κ_img — giả định
CONFORMAL_ALPHA = 0.01  # File 3 §1.2


class EscalationReason(StrEnum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    HIGH_TOKEN_ENTROPY = "HIGH_TOKEN_ENTROPY"  # noqa: S105 -- mã lý do, không phải bí mật
    AMBIGUOUS_DIGIT = "AMBIGUOUS_DIGIT"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    BRAND_CONFLICT = "BRAND_CONFLICT"
    CONFORMAL_SCORE = "CONFORMAL_SCORE"
    LOW_IMAGE_QUALITY = "LOW_IMAGE_QUALITY"
    PROMPT_INJECTION_SUSPECTED = "PROMPT_INJECTION_SUSPECTED"
    TRUNCATED_OUTPUT = "TRUNCATED_OUTPUT"
    PACKAGE_AMBIGUOUS = "PACKAGE_AMBIGUOUS"
    MISSING_CRITICAL = "MISSING_CRITICAL"


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    escalate: bool
    reasons: tuple[EscalationReason, ...]
    calibrated_confidence: float
    max_entropy_bits: float
    nonconformity_score: float

    @property
    def needs_user_confirmation(self) -> bool:
        return (
            self.calibrated_confidence < USER_CONFIRM_BELOW
            or EscalationReason.MISSING_CRITICAL in self.reasons
        )


def split_conformal_quantile(scores: Sequence[float], alpha: float = CONFORMAL_ALPHA) -> float:
    """``q̂ = điểm thứ ⌈(n+1)(1−α)⌉`` của tập hiệu chỉnh; ``inf`` nếu tập quá nhỏ."""
    if not 0 < alpha < 1:
        raise ValueError("alpha phải trong (0, 1)")
    n = len(scores)
    rank = math.ceil((n + 1) * (1 - alpha))
    if n == 0 or rank > n:
        return math.inf
    return sorted(scores)[rank - 1]


def total_mismatch(extraction: MultiImageLoanExtraction) -> bool:
    """``|TotalStated − Σ period_count·amount| > 2%·P_nominal`` (số nguyên, không float)."""
    total = extraction.total_repayment_stated_vnd
    principal = extraction.nominal_principal_vnd
    if total is None or principal is None or not extraction.repayment_regimes:
        return False
    schedule = sum(r.period_count * r.amount_per_period_vnd for r in extraction.repayment_regimes)
    return abs(total - schedule) * 100 > TOTAL_MISMATCH_PERCENT * principal


_TERMS_SCREENS = frozenset({ScreenCategory.CONTRACT_TERMS, ScreenCategory.DISBURSEMENT_SUMMARY})


def missing_critical(extraction: MultiImageLoanExtraction) -> bool:
    """Trang hợp đồng/giải ngân mà thiếu CẢ gốc lẫn thực nhận, hoặc LLM nghi thiếu trang.

    Không để độ tin cậy tự khai cao (0.95) che việc mọi số trọng yếu đều ``null``.
    """
    if extraction.missing_critical_page_suspected:
        return True
    return (
        bool(_TERMS_SCREENS & set(extraction.screen_categories))
        and extraction.nominal_principal_vnd is None
        and extraction.net_disbursed_vnd is None
    )


def _normalize_brand(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def brand_conflict(extraction: MultiImageLoanExtraction) -> bool:
    brands = {_normalize_brand(b) for b in extraction.distinct_loan_brands_detected}
    brands.discard("")
    return len(brands) >= 2


class ConformalRouter:
    def __init__(
        self,
        *,
        confidence_floor: float = CONFIDENCE_FLOOR,
        entropy_threshold_bits: float = ENTROPY_THRESHOLD_BITS,
        digit_margin_min: float = DIGIT_MARGIN_MIN,
        image_quality_floor: float = IMAGE_QUALITY_FLOOR,
        q_hat: float | None = None,
    ) -> None:
        self.confidence_floor = confidence_floor
        self.entropy_threshold_bits = entropy_threshold_bits
        self.digit_margin_min = digit_margin_min
        self.image_quality_floor = image_quality_floor
        self.q_hat = q_hat

    @classmethod
    def calibrated(
        cls, calibration_scores: Sequence[float], alpha: float = CONFORMAL_ALPHA
    ) -> ConformalRouter:
        return cls(q_hat=split_conformal_quantile(calibration_scores, alpha))

    def decide(
        self,
        extraction: MultiImageLoanExtraction,
        *,
        uncertainties: Sequence[DigitUncertainty] = (),
        image_quality: float | None = None,
        extra_reasons: Iterable[EscalationReason] = (),
    ) -> RoutingDecision:
        reasons: list[EscalationReason] = []
        crit = critical_uncertainties(uncertainties)
        confidence = extraction.critical_fields_min_confidence
        if crit:
            confidence = min(confidence, min(u.top1_prob for u in crit))
        if any(u.margin < self.digit_margin_min for u in crit):
            confidence = min(confidence, AMBIGUOUS_DIGIT_CONFIDENCE)
            reasons.append(EscalationReason.AMBIGUOUS_DIGIT)
        max_entropy = max((u.entropy_bits for u in crit), default=0.0)
        score = 1.0 - confidence

        if is_non_loan_only(extraction):
            # EC-AI10: ảnh ngoài phạm vi — không leo thang, không bịa số.
            return RoutingDecision(False, (), confidence, max_entropy, score)

        if max_entropy > self.entropy_threshold_bits:
            reasons.append(EscalationReason.HIGH_TOKEN_ENTROPY)
        if confidence < self.confidence_floor:
            reasons.append(EscalationReason.LOW_CONFIDENCE)
        if total_mismatch(extraction):
            reasons.append(EscalationReason.TOTAL_MISMATCH)
        if missing_critical(extraction):
            reasons.append(EscalationReason.MISSING_CRITICAL)
        if brand_conflict(extraction):
            reasons.append(EscalationReason.BRAND_CONFLICT)
        if self.q_hat is not None and score > self.q_hat:
            reasons.append(EscalationReason.CONFORMAL_SCORE)
        if image_quality is not None and image_quality < self.image_quality_floor:
            reasons.append(EscalationReason.LOW_IMAGE_QUALITY)
        for extra in extra_reasons:
            if extra not in reasons:
                reasons.append(extra)
        return RoutingDecision(bool(reasons), tuple(reasons), confidence, max_entropy, score)
