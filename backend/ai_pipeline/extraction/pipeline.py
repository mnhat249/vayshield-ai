"""Điều phối Trạm 2 tầng trích xuất: Flash → (Conformal) → crop ROI → Pro (File 3 §1.2).

Đầu ra chỉ là số liệu thô đã kiểm; không tính lãi, không phán quyết (D2). Hợp nhất kết quả Pro
không bao giờ hạ cờ cảnh báo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .cascade import CascadeResult, ExtractionUnavailableError, ModelAttempt, ModelCascade
from .conformal import ConformalRouter, EscalationReason, RoutingDecision
from .disentangler import (
    DisentangleResult,
    FeeInterval,
    LoanCard,
    MultiLoanDisentangler,
    PageExtraction,
    collapse_label_variants,
    deducted_fee_interval,
    fee_key,
)
from .gemini_client import ImagePart
from .prompts import (
    ESCALATION_SUFFIX,
    SYSTEM_PROMPT_V2,
    build_user_parts,
    sanitize_vi_text,
    scan_prompt_injection,
)
from .sanity import apply_magnitude_sanity, check_package_plausibility
from .schemas import (
    DeductedFeeItem,
    MultiImageLoanExtraction,
    SpecialScamMorphology,
    is_non_loan_only,
)
from .uncertainty import digit_uncertainties

NON_LOAN_MESSAGE_VI = "Ảnh này không phải hợp đồng vay hay tin nhắn tài chính."
INJECTION_NOTE_VI = "Ảnh có câu lệnh lạ tìm cách điều khiển máy đọc — đã bỏ qua, không làm đổi số."
TRUNCATED_NOTE_VI = "Kết quả đọc bị ngắt giữa chừng — vui lòng kiểm tra lại các con số."

OutcomeStatus = Literal["OK", "NON_LOAN", "NEEDS_LOAN_SELECTION"]
RoiCropper = Callable[
    [Sequence[ImagePart], MultiImageLoanExtraction, RoutingDecision], Sequence[ImagePart]
]

_PRO_NUMERIC = (
    "nominal_principal_vnd",
    "net_disbursed_vnd",
    "total_repayment_stated_vnd",
    "tenure_days_total",
    "stated_interest_rate_text",
)
_WARN_FLAGS = (
    "is_loan_extension_rollover",
    "bait_and_switch_detected",
    "missing_critical_page_suspected",
)


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    status: OutcomeStatus
    cards: tuple[LoanCard, ...]
    phase1_decision: RoutingDecision | None
    final_decision: RoutingDecision | None
    escalated: bool
    needs_user_confirmation: bool
    fee_interval: FeeInterval | None
    models_used: tuple[str, ...]
    attempts: tuple[ModelAttempt, ...]
    soft_timeout_fired: bool = False
    prompt_injection_suspected: bool = False
    truncated_repaired: bool = False
    scaled_fields: tuple[str, ...] = ()
    suspicious_fields: tuple[str, ...] = ()
    package_issues: tuple[str, ...] = ()
    message_vi: str | None = None

    @property
    def extraction(self) -> MultiImageLoanExtraction | None:
        return self.cards[0].extraction if len(self.cards) == 1 else None


def merge_fee_lists(
    flash: Sequence[DeductedFeeItem], pro: Sequence[DeductedFeeItem]
) -> list[DeductedFeeItem]:
    """Hợp phí Flash (toàn ảnh) với Pro (chỉ vùng ROI) — không bao giờ ít khoản hơn Flash.

    Ghép theo nhãn chuẩn hóa: cùng nhãn → lấy số của Pro (độ phân giải cao) nhưng cận trên
    ``uncertainty_max_vnd`` giữ max của cả hai để worst_case không bị hạ; tích sẵn OR.
    Khoản chỉ Pro thấy được thêm vào; khoản chỉ Flash thấy (ngoài ROI) được giữ nguyên.
    """
    remaining = collapse_label_variants(pro)
    out: list[DeductedFeeItem] = []
    for f in collapse_label_variants(flash):
        key = fee_key(f.fee_label)
        match = next((p for p in remaining if fee_key(p.fee_label) == key), None)
        if match is None:
            out.append(f)
            continue
        remaining.remove(match)
        bounds = [f.amount_vnd, match.amount_vnd]
        bounds += [v for v in (f.uncertainty_max_vnd, match.uncertainty_max_vnd) if v is not None]
        upper = max(bounds)
        out.append(
            DeductedFeeItem(
                fee_label=match.fee_label,
                amount_vnd=match.amount_vnd,
                uncertainty_max_vnd=upper if upper > match.amount_vnd else None,
                is_mandatory_or_prechecked=f.is_mandatory_or_prechecked
                or match.is_mandatory_or_prechecked,
            )
        )
    out.extend(remaining)
    return out


def merge_escalation(
    flash: MultiImageLoanExtraction, pro: MultiImageLoanExtraction
) -> MultiImageLoanExtraction:
    """Pro (ảnh ROI độ phân giải cao) thắng ở số liệu; cờ cảnh báo OR — không hạ (D2)."""
    data: dict[str, Any] = flash.model_dump()
    for name in _PRO_NUMERIC:
        value = getattr(pro, name)
        if value is not None:
            data[name] = value
    if pro.repayment_regimes:
        data["repayment_regimes"] = [r.model_dump() for r in pro.repayment_regimes]
    data["deducted_fees_at_source"] = [
        f.model_dump()
        for f in merge_fee_lists(flash.deducted_fees_at_source, pro.deducted_fees_at_source)
    ]
    for flag in _WARN_FLAGS:
        data[flag] = getattr(flash, flag) or getattr(pro, flag)
    up_f, up_p = flash.upfront_fee_demand, pro.upfront_fee_demand
    chosen = up_p if up_p.demanded else up_f
    data["upfront_fee_demand"] = chosen.model_dump() | {
        "demanded": up_f.demanded or up_p.demanded,
        "fake_frozen_receipt_claimed": up_f.fake_frozen_receipt_claimed
        or up_p.fake_frozen_receipt_claimed,
    }
    if flash.special_morphology is SpecialScamMorphology.NONE:
        data["special_morphology"] = pro.special_morphology
    data["distinct_loan_brands_detected"] = list(
        dict.fromkeys([*flash.distinct_loan_brands_detected, *pro.distinct_loan_brands_detected])
    )
    data["critical_fields_min_confidence"] = pro.critical_fields_min_confidence
    data["extraction_notes_vi"] = list(
        dict.fromkeys([*flash.extraction_notes_vi, *pro.extraction_notes_vi])
    )
    return MultiImageLoanExtraction.model_validate(data)


def _untrusted_fields(e: MultiImageLoanExtraction) -> list[str | None]:
    u = e.upfront_fee_demand
    return [
        *e.extraction_notes_vi,
        e.stated_interest_rate_text,
        e.late_penalty_text,
        e.claimed_lender_name,
        e.claimed_company_legal_name,
        u.verbatim_evidence,
        u.coercion_reason,
        u.fee_name_claimed,
        *(f.fee_label for f in e.deducted_fees_at_source),
    ]


def _with_notes(
    e: MultiImageLoanExtraction, extra: Sequence[str], *, cap_confidence: float | None = None
) -> MultiImageLoanExtraction:
    notes = [sanitize_vi_text(n) for n in dict.fromkeys([*e.extraction_notes_vi, *extra])]
    update: dict[str, Any] = {"extraction_notes_vi": notes}
    if cap_confidence is not None:
        update["critical_fields_min_confidence"] = min(
            e.critical_fields_min_confidence, cap_confidence
        )
    return e.model_copy(update=update)


class ExtractionPipeline:
    def __init__(
        self,
        cascade: ModelCascade,
        *,
        router: ConformalRouter | None = None,
        disentangler: MultiLoanDisentangler | None = None,
        roi_cropper: RoiCropper | None = None,
    ) -> None:
        self._cascade = cascade
        self._router = router or ConformalRouter()
        self._disentangler = disentangler or MultiLoanDisentangler()
        self._roi = roi_cropper or (lambda images, _e, _d: images)

    async def run(
        self,
        images: Sequence[ImagePart],
        *,
        untrusted_texts: Sequence[str] = (),
        image_quality: float | None = None,
        unit_hint_texts: Sequence[str] = (),
        on_soft_timeout: Callable[[], None] | None = None,
    ) -> ExtractionOutcome:
        """Có thể ném ``ExtractionUnavailableError`` → caller hạ cấp Tầng 3/4."""
        r1 = await self._cascade.extract(
            system_instruction=SYSTEM_PROMPT_V2,
            parts=build_user_parts(images, untrusted_texts),
            on_soft_timeout=on_soft_timeout,
        )
        models = [r1.model_used]
        attempts = list(r1.attempts)
        ext = r1.extraction

        if is_non_loan_only(ext):  # EC-AI10: không bịa số
            return ExtractionOutcome(
                status="NON_LOAN",
                cards=(),
                phase1_decision=None,
                final_decision=None,
                escalated=False,
                needs_user_confirmation=False,
                fee_interval=None,
                models_used=tuple(models),
                attempts=tuple(attempts),
                soft_timeout_fired=r1.soft_timeout_fired,
                message_vi=NON_LOAN_MESSAGE_VI,
            )

        mag = apply_magnitude_sanity(ext, unit_hint_texts)
        ext = mag.extraction
        injection = bool(scan_prompt_injection([*untrusted_texts, *_untrusted_fields(ext)]))
        extra: list[EscalationReason] = []
        notes: list[str] = []
        if injection:
            extra.append(EscalationReason.PROMPT_INJECTION_SUSPECTED)
            notes.append(INJECTION_NOTE_VI)
        if r1.truncated_repaired:
            extra.append(EscalationReason.TRUNCATED_OUTPUT)
            notes.append(TRUNCATED_NOTE_VI)
        package_issues = check_package_plausibility(ext)
        if package_issues:
            extra.append(EscalationReason.PACKAGE_AMBIGUOUS)
        ext = _with_notes(ext, notes)
        decision = self._router.decide(
            ext,
            uncertainties=digit_uncertainties(r1.raw.tokens),
            image_quality=image_quality,
            extra_reasons=extra,
        )

        if EscalationReason.BRAND_CONFLICT in decision.reasons:
            result = await self._split_brands(images, untrusted_texts, ext, attempts, models)
            return ExtractionOutcome(
                status="NEEDS_LOAN_SELECTION" if result.requires_loan_selection else "OK",
                cards=result.cards,
                phase1_decision=decision,
                final_decision=decision,
                escalated=False,
                needs_user_confirmation=True,
                fee_interval=None,
                models_used=tuple(models),
                attempts=tuple(attempts),
                soft_timeout_fired=r1.soft_timeout_fired,
                prompt_injection_suspected=injection,
                scaled_fields=mag.scaled_fields,
                suspicious_fields=mag.suspicious_fields,
                package_issues=package_issues,
            )

        final_decision = decision
        escalated = False
        truncated = r1.truncated_repaired
        if decision.escalate:
            roi = self._roi(images, ext, decision)
            try:
                r2: CascadeResult = await self._cascade.escalate(
                    system_instruction=SYSTEM_PROMPT_V2 + ESCALATION_SUFFIX,
                    parts=build_user_parts(roi, untrusted_texts),
                )
            except ExtractionUnavailableError as exc:
                attempts.extend(exc.attempts)
            else:
                attempts.extend(r2.attempts)
                models.append(r2.model_used)
                escalated = True
                pro_ext = apply_magnitude_sanity(r2.extraction, unit_hint_texts).extraction
                ext = _with_notes(merge_escalation(ext, pro_ext), [])
                package_issues = check_package_plausibility(ext)
                pro_extra = [EscalationReason.PACKAGE_AMBIGUOUS] if package_issues else []
                if injection:
                    # S2-EC18: lệnh lạ vẫn nằm trong ảnh — Pro đọc lại không xóa được nghi vấn.
                    pro_extra.append(EscalationReason.PROMPT_INJECTION_SUSPECTED)
                truncated = truncated or r2.truncated_repaired  # EC-AI22: không hạ cờ
                if truncated:
                    pro_extra.append(EscalationReason.TRUNCATED_OUTPUT)
                final_decision = self._router.decide(
                    ext, uncertainties=digit_uncertainties(r2.raw.tokens), extra_reasons=pro_extra
                )

        # EC-AI15: chữ số sát nhau ở Pha 1 (< 0.55) → luôn ép xác nhận tay, kể cả khi Pro
        # tự khai đọc rõ (không tin điểm tự chấm của một model đơn lẻ).
        needs_confirm = (
            (final_decision.escalate if escalated else decision.escalate)
            or decision.needs_user_confirmation
            or final_decision.needs_user_confirmation
            or bool(mag.suspicious_fields)
            or bool(mag.scaled_fields)
            or truncated
        )
        disent = self._disentangler.disentangle([PageExtraction(0, ext)])
        return ExtractionOutcome(
            status="OK",
            cards=disent.cards,
            phase1_decision=decision,
            final_decision=final_decision,
            escalated=escalated,
            needs_user_confirmation=needs_confirm,
            fee_interval=deducted_fee_interval(ext.deducted_fees_at_source),
            models_used=tuple(models),
            attempts=tuple(attempts),
            soft_timeout_fired=r1.soft_timeout_fired,
            prompt_injection_suspected=injection,
            truncated_repaired=truncated,
            scaled_fields=mag.scaled_fields,
            suspicious_fields=mag.suspicious_fields,
            package_issues=package_issues,
        )

    async def _split_brands(
        self,
        images: Sequence[ImagePart],
        untrusted_texts: Sequence[str],
        merged: MultiImageLoanExtraction,
        attempts: list[ModelAttempt],
        models: list[str],
    ) -> DisentangleResult:
        """EC-AI07: nhiều thương hiệu → trích xuất từng ảnh riêng rồi tách thẻ, không gộp chéo."""
        if len(images) < 2:
            return self._disentangler.disentangle([PageExtraction(0, merged)])
        pages: list[PageExtraction] = []
        for idx, image in enumerate(images):
            try:
                r = await self._cascade.extract(
                    system_instruction=SYSTEM_PROMPT_V2,
                    parts=build_user_parts([image], untrusted_texts),
                )
            except ExtractionUnavailableError as exc:
                attempts.extend(exc.attempts)
                continue
            attempts.extend(r.attempts)
            models.append(r.model_used)
            pages.append(PageExtraction(idx, _with_notes(r.extraction, [])))
        if not pages:
            return self._disentangler.disentangle([PageExtraction(0, merged)])
        return self._disentangler.disentangle(pages)
