"""Ghép đôi người thân & thẻ giải cứu 2 chiều (File 4 §4.1, §3.1 vs:pair:code)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import RequestModel
from app.core.schemas.shield import EscalationChannel

PAIRING_CODE_PATTERN = r"^[A-HJ-NP-Z2-9]{6}$"  # 6 ký tự, bỏ I/O/0/1 dễ nhầm


class PairInitRequest(RequestModel):
    guardian_alias: str = Field(min_length=1, max_length=64, description="vd 'Con trai'")
    borrower_display_label: str | None = Field(
        default=None, max_length=32, description="Nhãn người thân thấy, vd 'Mẹ'"
    )


class PairInitResponse(BaseModel):
    link_id: UUID
    pairing_code: str = Field(pattern=PAIRING_CODE_PATTERN)
    qr_payload: str
    expires_at: datetime
    max_fail_attempts: int = Field(ge=1)


class WebPushKeys(RequestModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class WebPushSubscription(RequestModel):
    endpoint: str = Field(min_length=10, max_length=1000, pattern=r"^https://")
    keys: WebPushKeys


class FallbackContact(RequestModel):
    channel: Literal["SMS", "EMAIL"]
    value: str = Field(min_length=3, max_length=254)


class PairConfirmRequest(RequestModel):
    pairing_code: str = Field(pattern=PAIRING_CODE_PATTERN)
    consent_accepted: bool = Field(description="Phải là true (đồng thuận 2 chiều)")
    webpush_subscription: WebPushSubscription | None = None
    fallback_contact: FallbackContact | None = None


class PairConfirmResponse(BaseModel):
    link_id: UUID
    status: Literal["ACTIVE"]
    guardian_alias: str
    borrower_display_label: str | None = None
    guardian_session_token: str
    guardian_session_expires_at: datetime


class RescueCardType(StrEnum):
    CALL_ME_NOW = "CALL_ME_NOW"
    STOP_TRANSFER_NOW = "STOP_TRANSFER_NOW"
    I_AM_COMING = "I_AM_COMING"
    CALL_BANK_HOTLINE = "CALL_BANK_HOTLINE"


class SendRescueCardRequest(RequestModel):
    escalation_id: UUID | None = None
    card_type: RescueCardType
    message_vi: str | None = Field(default=None, max_length=280)


class SendRescueCardResponse(BaseModel):
    rescue_card_id: UUID
    delivered_via: list[EscalationChannel]
    queued: bool
    sent_at: datetime
