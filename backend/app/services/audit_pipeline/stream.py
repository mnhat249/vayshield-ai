"""Luồng SSE 5 pha của ``POST /audits/contract-stream`` (File 4 §4.2(A), File 2 §1.3/§3.2).

Chặng:  (1) làm sạch + độ mờ → ``audit.sanitized`` (đã xong TRƯỚC khi mở luồng, nên lỗi 415/413/422
là HTTP JSON chuẩn) → (2) VietQR ‖ (3) trích xuất LLM chạy SONG SONG → ``audit.vietqr_decoded``
→ ``audit.fields_extracted`` → (4–6) kernel T02 + ma trận T09 → ``audit.verdict_computed`` →
(7) lưu audit đã che PII ‖ đồng bộ HSM ‖ báo mule ‖ trích dẫn luật → ``audit.legal_explained``.

- EC-DB01: không giữ kết nối DB suốt luồng — ``audits.save`` mượn session ngắn ở cuối.
- EC-DB22: ``with_keepalive`` (ở route) chèn ``ping`` mỗi 2.5s trong lúc chờ LLM.
- S2-EC46: ``on_soft_timeout`` (4.5s) chỉ ghi nhận; cascade tự chuyển model dự phòng ở 6s.
- Mọi lỗi giữa luồng → ``audit.error`` là event CUỐI (EC-OPS23: không bao giờ 500/HTML), kèm
  ``fallback``: Tầng 4 = form 3 ô → ``/recalculate`` bằng chính ``audit_id`` đã phát ở pha 1
  (audit tối thiểu được lưu trước khi phát lỗi để ``/recalculate`` không bị 404).
- Số liệu mơ hồ (``needs_user_confirmation``) KHÔNG được đoán: khoảng APR thành "không xác định"
  (→ AMBER) + mã ``NEEDS_USER_CONFIRMATION`` trong ``contradictions``; client hỏi lại rồi gọi
  ``/recalculate``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable
from dataclasses import dataclass
from functools import partial
from typing import Any, Final
from uuid import UUID

from ai_pipeline.extraction import ExtractionOutcome, ExtractionPipeline, ExtractionUnavailableError
from ai_pipeline.extraction.gemini_client import ImagePart
from ai_pipeline.extraction.prompts import sanitize_vi_text
from app.core.errors import ErrorCode, current_trace_id
from app.core.logging import get_logger
from app.core.schemas.audits import ContractStreamMetadata
from app.core.schemas.common import TriggerOrigin, VerdictCode
from app.core.schemas.sse import (
    AuditError,
    AuditFieldsExtracted,
    AuditLegalExplained,
    AuditSanitized,
    AuditVerdictComputed,
    AuditVietqrDecoded,
    FallbackMode,
)
from app.core.sse import format_sse
from app.services.audit_pipeline.compute import assess
from app.services.audit_pipeline.container import AuditServices
from app.services.audit_pipeline.facts import LoanFacts, facts_from_extraction
from app.services.audit_pipeline.ingest import NO_QR, IngestResult, QrFinding, scan_qr
from app.services.audit_pipeline.masking import mask_account, mask_optional
from app.services.audit_pipeline.ports import MULE_REPORT_VERDICTS
from app.services.audit_pipeline.provenance import load_apk_result
from app.services.audit_pipeline.records import build_record
from app.services.realtime.models import BorrowerPrincipal

logger = get_logger("vayshield.audit_pipeline.stream")

NON_LOAN_MESSAGE_VI: Final = "Ảnh này không phải hợp đồng vay hay tin nhắn tài chính."
SELECT_ONE_LOAN_MESSAGE_VI: Final = (
    "Ảnh có nhiều khoản vay khác nhau. Hãy chọn ảnh của MỘT khoản vay để kiểm tra."
)
DEGRADED_MESSAGE_VI: Final = (
    "Máy đọc ảnh đang bận. Bạn nhập nhanh 3 ô: vay bao nhiêu, thực nhận bao nhiêu, "
    "trả bao nhiêu sau mấy ngày — hệ thống sẽ tính ngay."
)
INTERNAL_MESSAGE_VI: Final = "Hệ thống gặp sự cố khi phân tích. Bạn nhập nhanh 3 ô số liệu để tính."


@dataclass(frozen=True, slots=True)
class StreamJob:
    audit_id: UUID
    principal: BorrowerPrincipal
    meta: ContractStreamMetadata
    ingest: IngestResult
    wage_vnd: int
    elapsed_ms_at_start: int
    started: float  # đồng hồ đơn điệu lúc nhận request


def _error_event(
    code: ErrorCode, message: str, *, retryable: bool, fallback: FallbackMode | None
) -> str:
    return format_sse(
        "audit.error",
        AuditError(
            code=code,
            message_vi=message,
            trace_id=current_trace_id(),
            retryable=retryable,
            fallback=fallback,
        ),
    )


def _vietqr_event(qr: QrFinding, quorum_hit: bool) -> AuditVietqrDecoded:
    if not qr.found or qr.bank_bin is None or qr.account_no is None:
        return AuditVietqrDecoded(found=False)
    return AuditVietqrDecoded(
        found=True,
        bank_bin=qr.bank_bin,
        masked_account=mask_account(qr.account_no),
        mule_quorum_hit=quorum_hit,
        preset_amount_vnd=qr.amount_vnd,
    )


def _fields_event(facts: LoanFacts) -> AuditFieldsExtracted:
    reason = (
        mask_optional(sanitize_vi_text(facts.upfront_reason), 200) if facts.upfront_reason else None
    )
    return AuditFieldsExtracted(
        claimed_lender=mask_optional(facts.claimed_lender_name, 120),
        nominal_principal_vnd=facts.nominal_principal_vnd,
        net_disbursed_vnd=facts.net_disbursed_vnd,
        upfront_fee_demanded=facts.upfront_demanded,
        upfront_fee_amount_vnd=facts.upfront_amount_vnd,
        upfront_reason=reason,
        confidence=min(1.0, max(0.0, facts.confidence)),
    )


async def _extract(services: AuditServices, job: StreamJob) -> ExtractionOutcome | None:
    pipeline: ExtractionPipeline | None = services.pipeline_for(job.principal.is_sandbox)
    if pipeline is None:
        return None
    parts = [ImagePart(i.jpeg, "image/jpeg") for i in job.ingest.images]
    try:
        return await pipeline.run(parts, image_quality=job.ingest.quality)
    except ExtractionUnavailableError:
        return None


async def _persist_quietly(save: Awaitable[None]) -> bool:
    try:
        await save
    except Exception:
        logger.exception("audit_persist_failed")
        return False
    return True


async def contract_events(services: AuditServices, job: StreamJob) -> AsyncGenerator[str, None]:
    """Sinh các khung SSE. Không bao giờ ném lỗi ra ngoài: mọi lỗi → ``audit.error`` cuối."""
    tasks: list[asyncio.Future[Any]] = []
    try:
        yield format_sse(
            "audit.sanitized",
            AuditSanitized(
                audit_id=job.audit_id,
                image_count=len(job.ingest.images),
                blur_ok=job.ingest.blur_ok,
                elapsed_ms=job.elapsed_ms_at_start,
            ),
        )
        principal, meta = job.principal, job.meta
        origin = TriggerOrigin.SANDBOX_SIM if principal.is_sandbox else TriggerOrigin.STATION_2_SCAN
        event_id = str(job.audit_id)

        qr_task = asyncio.ensure_future(scan_qr(job.ingest.images, services.cpu))
        extract_task = asyncio.ensure_future(_extract(services, job))
        hook_task = asyncio.ensure_future(
            services.shield.on_submitted(
                principal.device_id, meta.fiber_key, TriggerOrigin.STATION_2_SCAN, event_id
            )
        )
        tasks += [qr_task, extract_task, hook_task]
        provenance = await load_apk_result(
            services.redis, meta.linked_apk_audit_id, principal.device_id
        )

        try:
            qr = await qr_task
        except Exception:
            logger.warning("qr_scan_failed")
            qr = NO_QR
        quorum_hit = (
            await services.mule.is_quorum_hit(qr.bank_bin, qr.account_no)
            if qr.found and qr.bank_bin and qr.account_no
            else False
        )
        yield format_sse("audit.vietqr_decoded", _vietqr_event(qr, quorum_hit))

        outcome = await extract_task
        if outcome is None:
            degraded = LoanFacts(
                ambiguous=True,
                confidence=0.0,
                apk_signature_ok=provenance.signature_ok,
                package_name=provenance.package_name,
            )
            await _save_degraded(services, job, degraded, qr.found)
            yield _error_event(
                ErrorCode.SERVICE_DEGRADED,
                DEGRADED_MESSAGE_VI,
                retryable=True,
                fallback=FallbackMode.MANUAL_3_FIELD_FORM,
            )
            return
        if outcome.status == "NON_LOAN":
            yield _error_event(
                ErrorCode.VALIDATION_ERROR,
                outcome.message_vi or NON_LOAN_MESSAGE_VI,
                retryable=True,
                fallback=FallbackMode.RETAKE_PHOTO,
            )
            return
        extraction = outcome.extraction
        if outcome.status != "OK" or extraction is None:
            yield _error_event(
                ErrorCode.VALIDATION_ERROR,
                SELECT_ONE_LOAN_MESSAGE_VI,
                retryable=True,
                fallback=FallbackMode.RETAKE_PHOTO,
            )
            return

        facts = facts_from_extraction(
            extraction,
            ambiguous=outcome.needs_user_confirmation,
            apk_signature_ok=provenance.signature_ok,
            package_name=provenance.package_name,
        )
        yield format_sse("audit.fields_extracted", _fields_event(facts))

        assessment = await services.cpu.run(
            partial(assess, facts, services.registry, daily_wage_vnd=job.wage_vnd)
        )
        verdict_event = assessment.verdict.to_audit_verdict_computed()
        verdict_event = AuditVerdictComputed.model_validate(
            {**verdict_event.model_dump(), "contradictions": list(assessment.contradiction_codes)}
        )
        yield format_sse("audit.verdict_computed", verdict_event)

        code = assessment.verdict.verdict_code
        cited_task = asyncio.ensure_future(services.legal.cited_clauses(code.value))
        tasks.append(cited_task)
        try:
            cited = await cited_task
        except Exception:
            cited = ()
        latency_ms = int((services.clock.monotonic() - job.started) * 1000)
        record = build_record(
            audit_id=job.audit_id,
            principal=principal,
            fiber_key=meta.fiber_key,
            origin=origin,
            image_count=len(job.ingest.images),
            facts=facts,
            assessment=assessment,
            wage_vnd=job.wage_vnd,
            qr_found=qr.found,
            cited=tuple(c.code for c in cited),
            latency_ms=latency_ms,
            model_tier=(outcome.models_used[-1] if outcome.models_used else "unknown"),
        )
        await asyncio.gather(hook_task, return_exceptions=True)  # E2 trước V1/V2
        side_effects: list[asyncio.Future[Any]] = [
            asyncio.ensure_future(_persist_quietly(services.audits.save(record))),
            asyncio.ensure_future(
                services.shield.on_verdict(principal.device_id, meta.fiber_key, code, event_id)
            ),
        ]
        if (
            code in MULE_REPORT_VERDICTS
            and qr.found
            and qr.bank_bin
            and qr.account_no
            and not principal.is_sandbox
        ):
            side_effects.append(
                asyncio.ensure_future(
                    services.mule.report(
                        bank_bin=qr.bank_bin,
                        account_no=qr.account_no,
                        device_id=principal.device_id,
                        script_text=facts.upfront_reason or "",
                        impersonated_lender_id=(
                            assessment.match.record.registry_id if assessment.match.record else None
                        ),
                        now=services.clock.now(),
                    )
                )
            )
        tasks += side_effects
        await asyncio.gather(*side_effects, return_exceptions=True)  # audit đã lưu trước pha 5
        yield format_sse(
            "audit.legal_explained",
            AuditLegalExplained(
                headline_vi=assessment.verdict.headline_vi,
                tts_voice_script=assessment.verdict.tts_script,
                cited_clauses=list(cited),
            ),
        )
    except asyncio.CancelledError:
        raise  # client ngắt: dọn ở finally, không phát gì thêm
    except Exception:
        logger.exception("contract_stream_failed")
        yield _error_event(
            ErrorCode.INTERNAL_ERROR,
            INTERNAL_MESSAGE_VI,
            retryable=True,
            fallback=FallbackMode.MANUAL_3_FIELD_FORM,
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task


async def _save_degraded(
    services: AuditServices, job: StreamJob, facts: LoanFacts, qr_found: bool
) -> None:
    """Lưu audit tối thiểu (AMBER) để Tầng 4 gọi ``/recalculate`` với đúng ``audit_id``."""
    try:
        assessment = assess(facts, services.registry, daily_wage_vnd=job.wage_vnd)
        record = build_record(
            audit_id=job.audit_id,
            principal=job.principal,
            fiber_key=job.meta.fiber_key,
            origin=TriggerOrigin.STATION_2_SCAN,
            image_count=len(job.ingest.images),
            facts=facts,
            assessment=assessment,
            wage_vnd=job.wage_vnd,
            qr_found=qr_found,
            cited=(),
            latency_ms=int((services.clock.monotonic() - job.started) * 1000),
            model_tier="NONE",
        )
        await services.audits.save(record)
    except Exception:
        logger.exception("degraded_audit_persist_failed")


__all__ = ["StreamJob", "VerdictCode", "contract_events"]
