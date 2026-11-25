"""Bàn làm việc Fraud Ops B2B: cụm mule, hồ sơ SIMO, đăng nhập (File 4 §4.1, EC-OPS15)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import RequestModel


class SeverityLevel(StrEnum):
    WATCH = "WATCH"
    ELEVATED = "ELEVATED"
    CRITICAL_WAVE = "CRITICAL_WAVE"


class ScamTrap(StrEnum):
    TRAP_1_ADVANCE_FEE = "TRAP_1_ADVANCE_FEE"
    TRAP_2_SHARK_APK = "TRAP_2_SHARK_APK"
    TRAP_3_LOAN_RECALL = "TRAP_3_LOAN_RECALL"


class SimoDossierStatus(StrEnum):
    OBSERVING = "OBSERVING"
    QUORUM_REACHED = "QUORUM_REACHED"
    REVIEWED_CONFIRMED = "REVIEWED_CONFIRMED"
    EXPORTED_TO_SIMO = "EXPORTED_TO_SIMO"
    FALSE_POSITIVE_DISMISSED = "FALSE_POSITIVE_DISMISSED"


class EvidenceChannel(StrEnum):
    VIETQR_CRYPTO_DECODED = "VIETQR_CRYPTO_DECODED"
    OCR_RECEIPT_EXTRACTED = "OCR_RECEIPT_EXTRACTED"
    VOICE_CHAT_REPORTED = "VOICE_CHAT_REPORTED"


class MuleNode(BaseModel):
    node_id: UUID
    bank_bin: str
    masked_account_display: str
    distinct_device_quorum: int = Field(ge=0)
    threat_confidence: float = Field(ge=0, le=1)
    simo_dossier_status: SimoDossierStatus
    is_dynamic_va_range: bool


class MuleLink(BaseModel):
    source_node_id: UUID
    target_node_id: UUID
    relation: Literal["SAME_VA_PREFIX", "SHARED_SCAM_SCRIPT", "SHARED_REPORTER_WAVE"]


class MuleCluster(BaseModel):
    cluster_id: UUID
    campaign_label: str
    impersonated_lender: str | None = None
    dominant_scam_trap: ScamTrap
    severity_level: SeverityLevel
    is_simulated_seed: bool = Field(description="EC-OPS16: nhãn [Dữ liệu Mô phỏng Thực tiễn]")
    first_seen_at: datetime
    last_seen_at: datetime
    nodes: list[MuleNode]
    links: list[MuleLink] = Field(default_factory=list)


class MuleClusterPage(BaseModel):
    items: list[MuleCluster]
    next_cursor: str | None = None


class DossierEvidence(BaseModel):
    channel: EvidenceChannel
    reported_at: datetime
    masked_snippet_vi: str


class SimoDossier(BaseModel):
    dossier_id: UUID
    mule_node_id: UUID
    bank_bin: str
    masked_account_display: str
    account_holder_masked: str | None = None
    unsealed_account_number: str | None = Field(
        default=None, description="Chỉ có khi unseal=true; ghi vào chuỗi băm kiểm toán"
    )
    simo_dossier_status: SimoDossierStatus
    distinct_device_quorum: int = Field(ge=0)
    cluster_label: str | None = None
    evidence: list[DossierEvidence]
    advisory_draft_vi: str | None = None
    generated_at: datetime
    audit_chain_entry_hash: str | None = None


class OpsTokenRequest(RequestModel):
    grant_type: Literal["password", "refresh_token", "judge_quick_login"]
    username: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=256)
    refresh_token: str | None = Field(default=None, max_length=4096)
    judge_access_code: str | None = Field(default=None, max_length=128)


class OpsTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int = Field(ge=1)
    refresh_token: str
    refresh_expires_in: int = Field(ge=1)
    roles: list[str]
