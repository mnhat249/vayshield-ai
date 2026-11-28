"""Logic nghiệp vụ của các endpoint REST Trạm 1 & 2 (apk-verify, voice-terms, recalculate).

Route chỉ xác thực + chuyển tiếp; mọi phép tính tài chính / phán quyết là tất định (0 LLM).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Final
from uuid import UUID

from ai_pipeline.extraction.voice import transcribe_terms_to_extraction
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_uuid7
from app.core.logging import get_logger
from app.core.schemas.audits import (
    ApkVerdict,
    ApkVerifyRequest,
    ApkVerifyResponse,
    NormalizedTerms,
    RecalculateRequest,
    RecalculateResponse,
    SlangToken,
    VoiceTermsMetadata,
    VoiceTermsResponse,
)
from app.core.schemas.common import TriggerOrigin
from app.finance.livelihood import DEFAULT_DAILY_WAGE_VND
from app.services.audit_pipeline.apk import verify_apk
from app.services.audit_pipeline.compute import assess
from app.services.audit_pipeline.container import AuditServices
from app.services.audit_pipeline.facts import LoanFacts, RegimeFact, facts_from_extraction
from app.services.audit_pipeline.masking import mask_pii
from app.services.audit_pipeline.provenance import store_apk_result
from app.services.audit_pipeline.records import build_record
from app.services.realtime.models import BorrowerPrincipal

logger = get_logger("vayshield.audit_pipeline.service")

HOOK_TIMEOUT_S: Final = 0.3
STT_UNAVAILABLE_VI: Final = (
    "Máy chủ chưa nhận dạng được giọng nói. Hãy bật bản ghi trên máy hoặc nhập tay 3 con số."
)
DEFAULT_FIBER: Final = "default"


async def wage_for(services: AuditServices, principal: BorrowerPrincipal) -> int:
    try:
        device = await services.devices.get(principal.device_id)
    except Exception:
        logger.warning("wage_lookup_failed")
        return DEFAULT_DAILY_WAGE_VND
    return device.daily_wage_vnd if device is not None else DEFAULT_DAILY_WAGE_VND


# --------------------------------------------------------------------- Trạm 1 ---


async def apk_verify(
    services: AuditServices, principal: BorrowerPrincipal, req: ApkVerifyRequest
) -> ApkVerifyResponse:
    await services.ensure_ready()
    audit_id = new_uuid7()
    outcome = verify_apk(req, services.registry, services.trust, audit_id)
    await store_apk_result(
        services.redis, audit_id, principal.device_id, outcome.signature_ok, outcome.package_name
    )
    if outcome.response.apk_verdict in (
        ApkVerdict.LOOKALIKE_SPOOF,
        ApkVerdict.UNLICENSED_HIGH_RISK,
    ):
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                services.shield.on_apk(principal.device_id, DEFAULT_FIBER, str(audit_id)),
                HOOK_TIMEOUT_S,
            )
    return outcome.response


# --------------------------------------------------------------------- Trạm 2 ---


async def voice_terms(
    services: AuditServices,
    principal: BorrowerPrincipal,
    meta: VoiceTermsMetadata,
    *,
    audio: tuple[bytes, str] | None,
    transcript_text: str | None,
) -> VoiceTermsResponse:
    await services.ensure_ready()
    started = services.clock.monotonic()
    transcript = (transcript_text or "").strip()
    if not transcript:
        if audio is None or services.stt is None:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503, STT_UNAVAILABLE_VI)
        try:
            transcript = (await services.stt.transcribe(audio[0], audio[1], meta.locale)).strip()
        except Exception:
            logger.warning("stt_failed")
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503, STT_UNAVAILABLE_VI) from None
        if not transcript:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503, STT_UNAVAILABLE_VI)

    draft = transcribe_terms_to_extraction(transcript)
    audit_id = new_uuid7()
    # Số mơ hồ (cụm tiền không đọc chắc / ghi chú hỏi lại) → KHÔNG đoán: APR "không xác định".
    ambiguous = any(m.value_vnd is None for m in draft.slang_tokens) or bool(
        draft.extraction.extraction_notes_vi
    )
    facts = facts_from_extraction(draft.extraction, ambiguous=ambiguous)
    wage = await wage_for(services, principal)
    assessment = assess(facts, services.registry, daily_wage_vnd=wage)
    cited = await services.legal.cited_clauses(assessment.verdict.verdict_code.value)

    record = build_record(
        audit_id=audit_id,
        principal=principal,
        fiber_key=meta.fiber_key,
        origin=TriggerOrigin.STATION_2_VOICE,
        image_count=0,
        facts=facts,
        assessment=assessment,
        wage_vnd=wage,
        qr_found=False,
        cited=tuple(c.code for c in cited),
        latency_ms=int((services.clock.monotonic() - started) * 1000),
        model_tier="voice-deterministic",
    )
    await services.shield.on_submitted(
        principal.device_id, meta.fiber_key, TriggerOrigin.STATION_2_VOICE, str(audit_id)
    )
    try:
        await services.audits.save(record)
    except Exception:
        logger.exception("voice_audit_persist_failed")
    with contextlib.suppress(Exception):
        await services.shield.on_verdict(
            principal.device_id,
            meta.fiber_key,
            assessment.verdict.verdict_code,
            str(audit_id),
        )

    strip = draft.confirmation
    return VoiceTermsResponse(
        audit_id=audit_id,
        transcript_masked=mask_pii(draft.transcript_normalized),
        normalized_terms=NormalizedTerms(
            nominal_principal_vnd=strip.principal_vnd,
            net_disbursed_vnd=strip.net_disbursed_vnd,
            total_repayment_vnd=strip.repayment_total_vnd,
            tenure_days=strip.tenure_days,
            upfront_fee_amount_vnd=facts.upfront_amount_vnd,
        ),
        # value_vnd None (mơ hồ) bị loại vì contract bắt buộc số nguyên; mơ hồ đã đẩy
        # needs_confirmation=True + ghi chú hỏi lại, nên người dùng vẫn bị hỏi.
        slang_tokens=[
            SlangToken(raw=mask_pii(m.raw)[:120], value_vnd=m.value_vnd)
            for m in draft.slang_tokens
            if m.value_vnd is not None
        ],
        needs_confirmation=draft.needs_confirmation or ambiguous,
        verdict=assessment.to_summary(tuple(cited)),
    )


async def recalculate(
    services: AuditServices,
    principal: BorrowerPrincipal,
    audit_id: UUID,
    req: RecalculateRequest,
) -> RecalculateResponse:
    """Giải lại dòng tiền tất định khi người dùng sửa số (< 15ms phần tính, 0 LLM)."""
    await services.ensure_ready()
    audit = await services.audits.get(audit_id, principal.device_id, principal.sandbox_namespace)
    if audit is None:  # không phải của thiết bị này / không tồn tại: cùng một câu trả lời 404
        raise AppError(ErrorCode.NOT_FOUND, 404)
    stored = LoanFacts.from_json(audit.context)
    wage = int(audit.context.get("wage") or DEFAULT_DAILY_WAGE_VND)

    t0 = time.perf_counter()
    facts = stored.with_user_numbers(
        nominal_principal_vnd=req.nominal_principal_vnd,
        net_disbursed_vnd=req.net_disbursed_vnd,
        total_repayment_vnd=req.total_repayment_vnd,
        tenure_days=req.tenure_days,
        regimes=[
            RegimeFact(r.period_count, r.period_interval_days, r.amount_per_period_vnd)
            for r in req.repayment_regimes
        ],
        deducted_fees_vnd=req.deducted_fees_vnd,
        upfront_fee_amount_vnd=req.upfront_fee_amount_vnd,
    )
    assessment = assess(facts, services.registry, daily_wage_vnd=wage)
    computed_ms = (time.perf_counter() - t0) * 1000

    new_cited = await services.legal.cited_clauses(assessment.verdict.verdict_code.value)
    updated = build_record(
        audit_id=audit.audit_id,
        principal=principal,
        fiber_key=audit.fiber_key,
        origin=audit.trigger_origin,
        image_count=audit.image_count,
        facts=facts,
        assessment=assessment,
        wage_vnd=wage,
        qr_found=audit.vietqr_extracted,
        cited=tuple(c.code for c in new_cited),
        latency_ms=audit.latency_ms,
        model_tier=audit.model_tier,
        user_edited=True,
    )
    try:
        await services.audits.update_recalculated(updated)
    except Exception:
        logger.exception("recalculate_persist_failed")
    return RecalculateResponse(
        audit_id=audit.audit_id,
        verdict=assessment.to_summary(new_cited),
        computed_in_ms=round(computed_ms, 3),
        llm_calls=0,
    )
