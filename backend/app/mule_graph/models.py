"""Mô hình miền của đồ thị Mule Pre-SIMO (File 4 §2.2: ``mule_account_nodes``,
``mule_incident_edges``, ``fraud_campaign_clusters``, ``b2b_disbursement_shields``).

Chỉ chứa dữ liệu thuần; không I/O. Số tài khoản không bao giờ nằm ở đây dạng rõ: chỉ có blind
index (``account_hmac``), chuỗi che (``masked_account_display``) và blob envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.core.schemas.b2b import B2BShieldStatus
from app.core.schemas.ops import EvidenceChannel, ScamTrap, SeverityLevel, SimoDossierStatus


@dataclass(slots=True)
class MuleNodeRecord:
    id: UUID
    bank_bin: str
    account_hmac: str
    va_prefix_hmac: str | None
    pepper_version: int
    masked_account_display: str
    account_cipher_envelope: bytes
    distinct_device_quorum: int = 1
    threat_confidence: float = 0.35
    simo_dossier_status: SimoDossierStatus = SimoDossierStatus.OBSERVING
    cluster_id: UUID | None = None
    is_dynamic_va_range: bool = False
    first_reported_at: datetime | None = None
    last_reported_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MuleEdgeRecord:
    mule_node_id: UUID
    reporter_device_id: UUID
    evidence_channel: EvidenceChannel
    created_at: datetime
    scam_script_vec: tuple[float, ...] | None = None


@dataclass(slots=True)
class ClusterRecord:
    id: UUID
    campaign_label: str
    dominant_scam_trap: ScamTrap
    severity_level: SeverityLevel
    first_seen_at: datetime
    last_seen_at: datetime
    incident_count_24h: int = 1
    centroid_embedding: tuple[float, ...] | None = None
    impersonated_lender_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PartnerRecord:
    """Đối tác B2B đã giải mã bí mật HMAC (repo giải envelope ``hmac_secret_cipher``)."""

    id: UUID
    hmac_secret: bytes = field(repr=False)
    rate_limit_rpm: int = 600
    is_active: bool = True
    lender_registry_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DeviceMatch:
    device_id: UUID
    phone_optin_exp_at: datetime | None


@dataclass(frozen=True, slots=True)
class ShieldRecord:
    id: UUID
    partner_id: UUID
    idempotency_key: str
    borrower_phone_hmac: str
    amount_bucket_tier: str
    shield_duration_hours: int
    status: B2BShieldStatus
    shield_expires_at: datetime
    created_at: datetime
    matched_device_id: UUID | None = None
