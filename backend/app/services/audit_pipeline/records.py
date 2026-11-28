"""Dựng ``AuditRecord`` (đã che PII) từ facts + phán quyết."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.schemas.common import TriggerOrigin, VerdictCode
from app.services.audit_pipeline.compute import Assessment
from app.services.audit_pipeline.facts import LoanFacts
from app.services.audit_pipeline.repository import AuditRecord
from app.services.realtime.models import BorrowerPrincipal
from app.verdict import MatchStatus
from app.verdict.lookalike import normalize_text

UNREGISTERED = "UNREGISTERED"


def build_record(
    *,
    audit_id: UUID,
    principal: BorrowerPrincipal,
    fiber_key: str,
    origin: TriggerOrigin,
    image_count: int,
    facts: LoanFacts,
    assessment: Assessment,
    wage_vnd: int,
    qr_found: bool,
    cited: tuple[str, ...],
    latency_ms: int,
    model_tier: str,
    user_edited: bool = False,
) -> AuditRecord:
    verdict = assessment.verdict
    computed = verdict.to_audit_verdict_computed()
    match = assessment.match
    exact = match.status is MatchStatus.EXACT and match.record is not None
    context: dict[str, Any] = {**facts.to_json(), "wage": wage_vnd}
    brand = (
        normalize_text(facts.claimed_lender_name)[:255]
        if facts.claimed_lender_name and normalize_text(facts.claimed_lender_name)
        else None
    )
    return AuditRecord(
        audit_id=audit_id,
        device_id=principal.device_id,
        sandbox_namespace=principal.sandbox_namespace,
        fiber_key=fiber_key,
        trigger_origin=origin,
        image_count=image_count,
        verdict_code=verdict.verdict_code.value,
        lender_category_resolved=(
            match.record.legal_category.value if exact and match.record else UNREGISTERED
        ),
        claimed_brand_normalized=brand,
        matched_lender_id=match.record.registry_id if exact and match.record else None,
        nominal_principal_vnd=facts.nominal_principal_vnd,
        net_disbursed_vnd=facts.net_disbursed_vnd,
        total_repayment_vnd=facts.total_repayment_vnd,
        tenure_days=facts.tenure_days,
        apr_vn_365=computed.apr_vn_365,
        apr_min_bound=computed.apr_min_bound,
        apr_max_bound=computed.apr_max_bound,
        ear_xirr=computed.ear_xirr,
        lost_workdays=computed.lost_workdays,
        upfront_fee_detected=facts.upfront_demanded
        or verdict.verdict_code is VerdictCode.V_RED_ADVANCE_FEE,
        brand_spoof_detected=verdict.verdict_code is VerdictCode.V_RED_BRAND_SPOOF,
        vietqr_extracted=qr_found,
        user_edited_numbers=user_edited,
        context=context,
        cited_clauses=cited,
        latency_ms=latency_ms,
        model_tier=model_tier[:32],
    )
