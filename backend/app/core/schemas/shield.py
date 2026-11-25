"""Trạm 3: Khiên 24h, đánh giá câu trả lời 10 trục, leo thang (File 4 §4.1, §4.2(C))."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import (
    AmountBucketTier,
    AudioCallState,
    HsmState,
    OntologyAction,
    OntologyAxis,
    RequestModel,
    TriggerOrigin,
)

FIBER_KEY_PATTERN = r"^[A-Za-z0-9_\-:.]{1,80}$"


class ShieldArmRequest(RequestModel):
    fiber_key: str = Field(default="default", pattern=FIBER_KEY_PATTERN)
    trigger_origin: TriggerOrigin
    amount_bucket_tier: AmountBucketTier | None = None
    disbursement_at: datetime | None = None
    shield_duration_hours: int = Field(default=24, ge=1, le=72)
    client_event_id: str = Field(min_length=8, max_length=128, description="Khóa idempotency")
    notif_dedupe_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 thông báo đã che (khử trùng SMS + Push trong 10 phút)",
    )


class ShieldArmResponse(BaseModel):
    session_id: UUID
    fiber_key: str
    hsm_state: HsmState
    shield_expires_at: datetime
    risk_log_odds: float
    deduplicated: bool


class SensorSnapshot(RequestModel):
    audio_call_state: AudioCallState
    screen_share_detected: bool = False
    hesitation_ms: int | None = Field(default=None, ge=0, le=600_000)
    bank_app_reopen_count: int = Field(default=0, ge=0, le=1000)
    seconds_since_disbursement: int | None = Field(default=None, ge=0)


class EvaluateResponseRequest(RequestModel):
    session_id: UUID
    challenge_id: str = Field(min_length=1, max_length=64)
    choice_id: str | None = Field(default=None, max_length=64)
    utterance_text: str | None = Field(
        default=None, max_length=1000, description="Lời khai (đã che SĐT/tên trên máy)"
    )
    sensors: SensorSnapshot


class DetectedAxis(BaseModel):
    axis: OntologyAxis
    name: str
    confidence: float = Field(ge=0, le=1)


class ChallengeChoice(BaseModel):
    choice_id: str
    label_vi: str


class Challenge(BaseModel):
    challenge_id: str
    prompt_vi: str
    choices: list[ChallengeChoice]


class EvaluateResponseResult(BaseModel):
    session_id: UUID
    action: OntologyAction
    detected_axes: list[DetectedAxis] = Field(default_factory=list)
    computed_scam_prob: float = Field(ge=0, le=1)
    next_hsm_state: HsmState
    message_vi: str
    tts_script_vi: str
    callback_hotline_hint_vi: str | None = Field(
        default=None, description="S2-EC47: gợi ý tự gọi lại hotline chính thức"
    )
    escalate_recommended: bool
    follow_up_challenge: Challenge | None = None


class EscalationReason(StrEnum):
    COERCION_DETECTED = "COERCION_DETECTED"
    SMURF_BYPASS_ATTEMPT = "SMURF_BYPASS_ATTEMPT"
    USER_CONFIRMED_SCAM = "USER_CONFIRMED_SCAM"
    USER_ALREADY_TRANSFERRED = "USER_ALREADY_TRANSFERRED"


class EscalationChannel(StrEnum):
    WEBSOCKET = "WEBSOCKET"
    WEB_PUSH = "WEB_PUSH"
    SMS = "SMS"
    EMAIL = "EMAIL"


class EscalateRequest(RequestModel):
    """Zero-Amount Safe Envelope: KHÔNG có số tiền, tên công ty vay, STK."""

    session_id: UUID
    reason: EscalationReason
    hsm_state: HsmState
    detected_axes: list[OntologyAxis] = Field(default_factory=list, max_length=10)
    client_event_id: str = Field(min_length=8, max_length=128)
    occurred_at: datetime


class EscalateResponse(BaseModel):
    escalation_id: UUID
    status: str = Field(description="PENDING_ACK | NO_ACTIVE_GUARDIAN")
    channels_attempted: list[EscalationChannel]
    guardians_targeted: int = Field(ge=0)
    ack_deadline_at: datetime
