"""Payload các sự kiện SSE (File 4 §4.2(A) — 5 pha + ping + error; luồng Ops live feed).

Nguồn sinh ``contracts/sse-events.schema.json``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.errors import ErrorCode
from app.core.schemas.common import CitedClause, KernelSignal, Severity, VerdictCode, Vnd


class AuditSanitized(BaseModel):
    audit_id: UUID
    image_count: int = Field(ge=1, le=4)
    blur_ok: bool
    elapsed_ms: int = Field(ge=0)


class AuditVietqrDecoded(BaseModel):
    found: bool
    bank_bin: str | None = Field(default=None, pattern=r"^\d{6}$")
    masked_account: str | None = None
    mule_quorum_hit: bool = False
    preset_amount_vnd: Vnd | None = None


class AuditFieldsExtracted(BaseModel):
    claimed_lender: str | None = None
    nominal_principal_vnd: Vnd | None = None
    net_disbursed_vnd: Vnd | None = None
    upfront_fee_demanded: bool
    upfront_fee_amount_vnd: Vnd | None = None
    upfront_reason: str | None = None
    confidence: float = Field(ge=0, le=1)


class AuditVerdictComputed(BaseModel):
    verdict_code: VerdictCode
    severity: Severity
    kernel_signal: KernelSignal | None = None
    apr_vn_365: float | None = None
    apr_min_bound: float | None = None
    apr_max_bound: float | None = None
    ear_xirr: float | None = None
    lost_workdays: float | None = Field(default=None, ge=0)
    official_hotline_verify: str | None = None
    contradictions: list[str] = Field(default_factory=list)


class AuditLegalExplained(BaseModel):
    headline_vi: str
    tts_voice_script: str
    cited_clauses: list[CitedClause] = Field(default_factory=list)


class Ping(BaseModel):
    elapsed_ms: int = Field(ge=0)


class FallbackMode(StrEnum):
    MANUAL_3_FIELD_FORM = "MANUAL_3_FIELD_FORM"  # File 2 §3.1 Tầng 4
    RETAKE_PHOTO = "RETAKE_PHOTO"
    VOICE_TERMS = "VOICE_TERMS"


class AuditError(BaseModel):
    """Lỗi giữa luồng (không thể đổi HTTP status sau khi đã stream) — luôn là event cuối."""

    code: ErrorCode
    message_vi: str
    trace_id: str
    retryable: bool
    fallback: FallbackMode | None = None


# Tên event → model, đúng thứ tự phát của luồng hợp đồng.
CONTRACT_STREAM_PHASES: Final[tuple[tuple[str, type[BaseModel]], ...]] = (
    ("audit.sanitized", AuditSanitized),
    ("audit.vietqr_decoded", AuditVietqrDecoded),
    ("audit.fields_extracted", AuditFieldsExtracted),
    ("audit.verdict_computed", AuditVerdictComputed),
    ("audit.legal_explained", AuditLegalExplained),
)
CONTRACT_STREAM_AUX: Final[tuple[tuple[str, type[BaseModel]], ...]] = (
    ("ping", Ping),
    ("audit.error", AuditError),
)


# ---------------------------------------------------- Ops live threat feed ---


class OpsThreatSignal(BaseModel):
    signal_id: UUID
    kind: Literal["MULE_REPORTED", "QUORUM_REACHED", "CLUSTER_ESCALATED", "B2B_SHIELD_ARMED"]
    cluster_id: UUID | None = None
    mule_node_id: UUID | None = None
    bank_bin: str | None = None
    masked_account_display: str | None = None
    severity_level: Literal["WATCH", "ELEVATED", "CRITICAL_WAVE"]
    occurred_at: datetime
    summary_vi: str


OPS_FEED_EVENTS: Final[tuple[tuple[str, type[BaseModel]], ...]] = (
    ("ops.threat_signal", OpsThreatSignal),
    ("ping", Ping),
)
