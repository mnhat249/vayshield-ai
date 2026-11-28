"""Chặng 4–6 của pipeline 7 chặng (File 2 §1.3): kernel T02 → khoảng APR → ma trận T09.

Hàm thuần, đồng bộ, không I/O, 0 LLM → dùng chung cho contract-stream, voice-terms và
``/recalculate`` (< 15ms). Mọi lỗi kernel được đổi thành "APR không xác định" (→ AMBER), không
bao giờ làm endpoint trả 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.core.schemas.common import CitedClause, VerdictSummary
from app.finance import (
    AdvanceFeeScamError,
    CashFlowPoint,
    FinanceError,
    LoanTerms,
    RateReport,
    RepaymentRegime,
    analyze_cashflows,
    build_cashflows,
    bullet_cashflows,
)
from app.finance.livelihood import DEFAULT_DAILY_WAGE_VND
from app.services.audit_pipeline.facts import LoanFacts
from app.verdict import (
    AprInterval,
    DeductedFee,
    LenderMatch,
    LenderRegistry,
    Provenance,
    UpfrontFeeDemand,
    VerdictInput,
    VerdictObject,
    decide_verdict,
    match_lender,
)

NEEDS_CONFIRMATION_CODE: Final = "NEEDS_USER_CONFIRMATION"
REASON_NEEDS_CONFIRMATION: Final = "NEEDS_USER_CONFIRMATION"
REASON_MISSING_TERMS: Final = "MISSING_REPAYMENT_TERMS"
REASON_BAD_NUMBERS: Final = "INCONSISTENT_NUMBERS"


@dataclass(frozen=True, slots=True)
class Assessment:
    verdict: VerdictObject
    interval: AprInterval
    match: LenderMatch
    needs_confirmation: bool
    contradiction_codes: tuple[str, ...]

    def to_summary(self, cited: tuple[CitedClause, ...] = ()) -> VerdictSummary:
        v = self.verdict.to_audit_verdict_computed()
        return VerdictSummary(
            verdict_code=v.verdict_code,
            severity=v.severity,
            kernel_signal=v.kernel_signal,
            apr_vn_365=v.apr_vn_365,
            apr_min_bound=v.apr_min_bound,
            apr_max_bound=v.apr_max_bound,
            ear_xirr=v.ear_xirr,
            lost_workdays=v.lost_workdays,
            official_hotline_verify=v.official_hotline_verify,
            contradictions=list(self.contradiction_codes),
            headline_vi=self.verdict.headline_vi,
            tts_voice_script=self.verdict.tts_script,
            cited_clauses=list(cited),
        )


def _deducted_scenarios(facts: LoanFacts) -> tuple[int, int] | None:
    """(khấu trừ tốt nhất, khấu trừ xấu nhất) tại nguồn; ``None`` nếu số liệu mâu thuẫn.

    Có ``net_disbursed`` → nó là sự thật (tiền thực nhận); phần chênh với gốc là khấu trừ. Không
    có → cộng phí đã đọc; phí bị mờ thì xấu nhất dùng ``uncertainty_max`` (S2-EC41).
    """
    principal, net = facts.nominal_principal_vnd, facts.net_disbursed_vnd
    if principal is None:
        return None
    if net is not None:
        if net > principal:
            return None
        gap = principal - net
        return gap, gap
    best = sum(f.amount_vnd for f in facts.fees)
    worst = sum(max(f.amount_vnd, f.uncertainty_max_vnd or 0) for f in facts.fees)
    return best, worst


def _flows_for(facts: LoanFacts, deducted: int) -> tuple[CashFlowPoint, ...]:
    principal = facts.nominal_principal_vnd
    if principal is None:
        raise FinanceError(REASON_MISSING_TERMS)
    if facts.regimes:
        return build_cashflows(
            LoanTerms(
                nominal_principal_vnd=principal,
                regimes=[
                    RepaymentRegime(r.period_count, r.period_interval_days, r.amount_per_period_vnd)
                    for r in facts.regimes
                ],
                deducted_at_source_vnd=deducted,
            )
        )
    if facts.total_repayment_vnd is not None and facts.tenure_days is not None:
        return bullet_cashflows(principal - deducted, facts.total_repayment_vnd, facts.tenure_days)
    raise FinanceError(REASON_MISSING_TERMS)


def compute_interval(
    facts: LoanFacts,
) -> tuple[AprInterval, tuple[CashFlowPoint, ...]]:
    """Khoảng ``[APR_min, APR_max]`` từ 2 kịch bản OCR (tốt/xấu nhất) + dòng tiền xấu nhất."""
    if facts.upfront_demanded:
        # Bẫy 1: đòi tiền túi trước khi giải ngân → kernel dừng, APR = ∞ (File 2 §4.2).
        return AprInterval.advance_fee(), ()
    if facts.ambiguous:
        return AprInterval.unavailable(REASON_NEEDS_CONFIRMATION), ()
    scenarios = _deducted_scenarios(facts)
    if scenarios is None:
        return AprInterval.unavailable(REASON_BAD_NUMBERS), ()
    reports: list[RateReport] = []
    worst_flows: tuple[CashFlowPoint, ...] = ()
    try:
        for deducted in dict.fromkeys(scenarios):  # bỏ trùng, giữ thứ tự (best, worst)
            flows = _flows_for(facts, deducted)
            reports.append(analyze_cashflows(flows))
            worst_flows = flows
    except AdvanceFeeScamError:
        return AprInterval.advance_fee(), ()
    except FinanceError as exc:
        reason = exc.code if exc.code == REASON_MISSING_TERMS else REASON_BAD_NUMBERS
        return AprInterval.unavailable(reason), ()
    return AprInterval.from_reports(reports), worst_flows


def to_verdict_input(
    facts: LoanFacts, cashflows: tuple[CashFlowPoint, ...], daily_wage_vnd: int
) -> VerdictInput:
    return VerdictInput(
        claimed_lender_name=facts.claimed_lender_name,
        claimed_company_legal_name=facts.claimed_company_legal_name,
        principal_vnd=facts.nominal_principal_vnd,
        net_disbursed_vnd=facts.net_disbursed_vnd,
        is_pawn_asset_secured=facts.is_pawn_asset_secured,
        special_morphology=facts.morphology,
        upfront_fee_demand=UpfrontFeeDemand(
            demanded=facts.upfront_demanded,
            amount_vnd=facts.upfront_amount_vnd,
            fake_frozen_receipt_claimed=facts.fake_frozen_receipt,
        ),
        deducted_fees=tuple(
            DeductedFee(
                label=f.label or "Phí",
                amount_vnd=f.amount_vnd,
                uncertainty_max_vnd=f.uncertainty_max_vnd,
                is_mandatory_or_prechecked=f.mandatory,
            )
            for f in facts.fees
        ),
        stated_annual_rate=facts.stated_annual_rate,
        missing_critical_page_suspected=facts.missing_critical_page,
        distinct_loan_brands_count=facts.distinct_brands,
        cashflows=cashflows,
        daily_wage_vnd=daily_wage_vnd,
    )


def assess(
    facts: LoanFacts,
    registry: LenderRegistry,
    *,
    daily_wage_vnd: int = DEFAULT_DAILY_WAGE_VND,
) -> Assessment:
    """Toàn bộ chặng tất định: facts → khoảng APR → đối chiếu pháp nhân → phán quyết."""
    interval, flows = compute_interval(facts)
    vin = to_verdict_input(facts, flows, daily_wage_vnd)
    provenance = Provenance(
        apk_signature_ok=facts.apk_signature_ok,
        package_name=facts.package_name,
        beneficiary_is_personal_account=facts.beneficiary_is_personal,
        account_frozen_notice=facts.frozen_notice,
    )
    match = match_lender(vin, provenance, registry)
    verdict = decide_verdict(vin, interval, match, provenance)
    codes = [c.value for c in verdict.contradictions]
    needs = facts.ambiguous
    if needs:
        codes.append(NEEDS_CONFIRMATION_CODE)
    return Assessment(verdict, interval, match, needs, tuple(codes))
