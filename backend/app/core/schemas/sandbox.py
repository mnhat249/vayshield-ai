"""Judge Sandbox Mode (File 4 §4.1, EC-DB16; File 6 §1.2 mục 3; File 5 §2.1)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import AmountBucketTier, AudioCallState, HsmState, RequestModel


class SandboxTier(StrEnum):
    PUBLIC = "PUBLIC"
    JUDGE_VIP = "JUDGE_VIP"


class SandboxSessionCreateRequest(RequestModel):
    vip_code: str | None = Field(default=None, max_length=64, description="vd mã ?vip= trên A4")


class SandboxSessionCreateResponse(BaseModel):
    sandbox_session_token: str = Field(description="Gửi lại qua header X-Sandbox-Session")
    sandbox_namespace: str
    tier: SandboxTier
    expires_at: datetime
    daily_budget_remaining_usd: float = Field(ge=0)


class SandboxEventType(StrEnum):
    BANK_DISBURSEMENT_NOTIF = "BANK_DISBURSEMENT_NOTIF"
    INCOMING_SCAM_CALL = "INCOMING_SCAM_CALL"
    CALL_ENDED = "CALL_ENDED"
    BANK_APP_OPENED = "BANK_APP_OPENED"
    B2B_DISBURSEMENT_WEBHOOK = "B2B_DISBURSEMENT_WEBHOOK"
    USER_ALREADY_TRANSFERRED = "USER_ALREADY_TRANSFERRED"
    RESET_SCENARIO = "RESET_SCENARIO"


class SandboxEventParams(RequestModel):
    amount_bucket_tier: AmountBucketTier | None = None
    call_state: AudioCallState | None = None
    screen_share: bool | None = None
    fiber_key: str = Field(default="default", pattern=r"^[A-Za-z0-9_\-:.]{1,80}$")


class SimulateEventRequest(RequestModel):
    event_type: SandboxEventType
    params: SandboxEventParams = Field(default_factory=SandboxEventParams)


class SimulateEventResponse(BaseModel):
    event_id: UUID
    sandbox_namespace: str
    accepted: bool
    resulting_hsm_state: HsmState
    risk_log_odds: float
    scam_probability: float = Field(ge=0, le=1)
    side_effects: list[str] = Field(default_factory=list)
