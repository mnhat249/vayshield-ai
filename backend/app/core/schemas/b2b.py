"""Webhook B2B bảo toàn quyền riêng tư (File 4 §4.2(B))."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import AmountBucketTier, RequestModel


class LoanProductType(StrEnum):
    """File 4 chỉ nêu CASH_CONSUMER_LOAN; các loại còn lại là giả định T00."""

    CASH_CONSUMER_LOAN = "CASH_CONSUMER_LOAN"
    POS_INSTALLMENT_LOAN = "POS_INSTALLMENT_LOAN"
    BNPL_CREDIT_LINE = "BNPL_CREDIT_LINE"
    CREDIT_CARD_CASH_ADVANCE = "CREDIT_CARD_CASH_ADVANCE"
    ASSET_SECURED_LOAN = "ASSET_SECURED_LOAN"
    OTHER = "OTHER"


class B2BShieldStatus(StrEnum):
    ARMED_ON_DEVICE = "ARMED_ON_DEVICE"
    PENDING_USER_ONLINE = "PENDING_USER_ONLINE"
    NO_DEVICE_OPTED_IN = "NO_DEVICE_OPTED_IN"


class DisbursementShieldRequest(RequestModel):
    borrower_phone_blind_hmac: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="HMAC-SHA256(pepper chia sẻ, SĐT E.164) — không bao giờ gửi SĐT thô",
    )
    amount_bucket_tier: AmountBucketTier
    product_type: LoanProductType
    shield_duration_hours: int = Field(default=24, ge=1, le=72)
    elevated_campaign_watch: bool = False


class PreSimoWaveWarning(BaseModel):
    impersonation_wave_active: bool
    active_clusters_targeting_brand: int = Field(ge=0)


class DisbursementShieldResponse(BaseModel):
    shield_id: UUID
    status: B2BShieldStatus
    shield_expires_at: datetime
    pre_simo_active_wave_warning: PreSimoWaveWarning
