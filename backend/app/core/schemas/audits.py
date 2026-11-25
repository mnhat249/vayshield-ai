"""Trạm 1 (APK) & Trạm 2 (hợp đồng, đọc miệng, tính lại) — File 4 §4.1, §4.2(A)."""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import (
    LegalCategory,
    RequestModel,
    Severity,
    VerdictSummary,
    Vnd,
    VndPositive,
)

# ----------------------------------------------------------------- Trạm 1 ---


class ApkVerdict(StrEnum):
    OFFICIAL_LICENSED = "OFFICIAL_LICENSED"
    LOOKALIKE_SPOOF = "LOOKALIKE_SPOOF"
    UNLICENSED_HIGH_RISK = "UNLICENSED_HIGH_RISK"
    UNKNOWN_LOW_RISK = "UNKNOWN_LOW_RISK"


class ApkVerifyRequest(RequestModel):
    package_name: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.]+$")
    signing_cert_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    app_label: str | None = Field(default=None, max_length=120)
    version_code: int | None = Field(default=None, ge=0)
    installer_package: str | None = Field(
        default=None, max_length=255, description="InstallSourceInfo; null = cài ngoài luồng"
    )
    requested_permissions: list[str] = Field(default_factory=list, max_length=300)


class MatchedLender(BaseModel):
    entity_code: str
    official_short_name: str
    legal_category: LegalCategory
    official_hotlines: list[str] = Field(default_factory=list)


class ApkVerifyResponse(BaseModel):
    audit_id: UUID
    apk_verdict: ApkVerdict
    severity: Severity
    matched_lender: MatchedLender | None = None
    lookalike_of: str | None = Field(default=None, description="Tên thương hiệu bị nhái")
    dangerous_permissions: list[str] = Field(default_factory=list)
    reasons_vi: list[str] = Field(default_factory=list)
    recommend_contract_scan: bool


# ----------------------------------------------------------------- Trạm 2 ---


class CaptureSource(StrEnum):
    SCREENSHOT = "SCREENSHOT"
    CAMERA = "CAMERA"
    SHARE_INTENT = "SHARE_INTENT"
    GALLERY = "GALLERY"
    SANDBOX_SAMPLE = "SANDBOX_SAMPLE"


class ContractStreamMetadata(RequestModel):
    """Nội dung trường multipart ``metadata_json`` của /audits/contract-stream."""

    capture_source: CaptureSource
    linked_apk_audit_id: UUID | None = None
    fiber_key: str = Field(default="default", max_length=80, pattern=r"^[A-Za-z0-9_\-:.]+$")
    client_compressed: bool = True
    sandbox_sample_id: str | None = Field(default=None, max_length=64)


class VoiceTermsMetadata(RequestModel):
    """Nội dung trường multipart ``metadata_json`` của /audits/voice-terms."""

    fiber_key: str = Field(default="default", max_length=80, pattern=r"^[A-Za-z0-9_\-:.]+$")
    locale: str = Field(default="vi-VN", max_length=10)
    dialect_hint: str | None = Field(default=None, max_length=16, description="BAC/TRUNG/NAM")


class NormalizedTerms(BaseModel):
    nominal_principal_vnd: VndPositive | None = None
    net_disbursed_vnd: Vnd | None = None
    total_repayment_vnd: VndPositive | None = None
    tenure_days: int | None = Field(default=None, ge=1, le=3650)
    upfront_fee_amount_vnd: Vnd | None = None


class SlangToken(BaseModel):
    raw: str = Field(description="Cụm từ gốc, vd '5 củ'")
    value_vnd: Vnd


class VoiceTermsResponse(BaseModel):
    audit_id: UUID
    transcript_masked: str = Field(description="Bản ghi lời nói đã che PII")
    normalized_terms: NormalizedTerms
    slang_tokens: list[SlangToken] = Field(default_factory=list)
    needs_confirmation: bool
    verdict: VerdictSummary


class RepaymentRegime(RequestModel):
    period_count: int = Field(ge=1, le=360)
    period_interval_days: int = Field(ge=1, le=365)
    amount_per_period_vnd: Vnd


class RecalculateRequest(RequestModel):
    """Người dùng sửa số (hoặc form 3 ô khi AI hạ cấp Tầng 4). Không gọi LLM."""

    nominal_principal_vnd: VndPositive | None = None
    net_disbursed_vnd: Vnd
    total_repayment_vnd: VndPositive | None = None
    tenure_days: int | None = Field(default=None, ge=1, le=3650)
    repayment_regimes: list[RepaymentRegime] = Field(default_factory=list, max_length=12)
    deducted_fees_vnd: Vnd = 0
    upfront_fee_amount_vnd: Vnd = 0
    edited_fields: list[str] = Field(default_factory=list, max_length=20)


class RecalculateResponse(BaseModel):
    audit_id: UUID
    verdict: VerdictSummary
    computed_in_ms: float = Field(ge=0)
    llm_calls: int = Field(ge=0, le=0, description="Luôn 0 — tính tất định")
