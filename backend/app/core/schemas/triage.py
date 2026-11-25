"""Cấp cứu Giờ Vàng S9 + TTS tiếng Việt (File 4 §4.1, team_division WP1-09)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import AmountBucketTier, RequestModel


class ScamChannel(StrEnum):
    PHONE_CALL = "PHONE_CALL"
    ZALO_OR_OTT = "ZALO_OR_OTT"
    SMS = "SMS"
    FAKE_LOAN_APP = "FAKE_LOAN_APP"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    OTHER = "OTHER"


class GoldenHourDossierRequest(RequestModel):
    session_id: UUID | None = None
    victim_bank_bin: str | None = Field(
        default=None, pattern=r"^\d{6}$", description="BIN ngân hàng nạn nhân đã chuyển tiền đi"
    )
    mule_bank_bin: str | None = Field(default=None, pattern=r"^\d{6}$")
    mule_masked_account: str | None = Field(default=None, max_length=32)
    transferred_at: datetime
    amount_bucket_tier: AmountBucketTier | None = None
    scam_channel: ScamChannel


class BankFreezeHotline(BaseModel):
    bank_bin: str | None = None
    bank_short_name: str
    hotline: str
    verified: bool = Field(description="False nếu hotline chưa được người kiểm chứng")


class GoldenHourDossierResponse(BaseModel):
    dossier_id: UUID
    minutes_since_transfer: int = Field(ge=0)
    golden_hour_remaining_minutes: int = Field(ge=0)
    bank_freeze_hotlines: list[BankFreezeHotline]
    police_hotline: str
    steps_vi: list[str]
    police_report_draft_vi: str
    pdf_available: bool


class TtsVoice(StrEnum):
    VI_FEMALE_NORTH = "VI_FEMALE_NORTH"
    VI_FEMALE_SOUTH = "VI_FEMALE_SOUTH"
    VI_MALE_NORTH = "VI_MALE_NORTH"


class TtsRequest(RequestModel):
    text_vi: str = Field(min_length=1, max_length=1000)
    voice: TtsVoice = TtsVoice.VI_FEMALE_SOUTH
    speaking_rate: float = Field(default=0.9, ge=0.5, le=1.5)


class TtsResponse(BaseModel):
    normalized_text_vi: str = Field(description="EC-AI26: đã đọc đúng viết tắt/số tiền")
    engine: Literal["SERVER_TTS", "CLIENT_FALLBACK"]
    audio_mime: str | None = None
    audio_base64: str | None = None
