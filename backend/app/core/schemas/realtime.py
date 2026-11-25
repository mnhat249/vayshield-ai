"""WebSocket hub ``/ws/v1/guardian`` + vé 1 lần (File 4 §4.1, §4.2(C), EC-DB12).

Nguồn sinh ``contracts/ws-protocol.json``. Mọi frame dùng chung phong bì
``{"type", "frame_id", "sent_at", "payload"}``. Payload cảnh báo tuân thủ
Zero-Amount Privacy Envelope: không số tiền, không tên công ty vay, không STK.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas.common import HsmState, OntologyAxis
from app.core.schemas.family import RescueCardType
from app.core.schemas.shield import EscalationChannel


class RealtimeRole(StrEnum):
    BORROWER = "BORROWER"
    GUARDIAN = "GUARDIAN"


class WsTicketResponse(BaseModel):
    ticket: str = Field(description="Vé 1 lần, xóa bằng GETDEL khi nâng cấp 101")
    role: RealtimeRole
    expires_at: datetime
    ttl_seconds: int = Field(ge=1)
    ws_path: str = Field(description="Đường dẫn WS, client tự suy wss:/ws: (EC-OPS22)")


class _StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -------------------------------------------------------- server → client ---


class ServerHello(_StrictPayload):
    connection_id: UUID
    role: RealtimeRole
    heartbeat_interval_s: int = Field(ge=1)
    ack_timeout_s: int = Field(ge=1)


class EmergencyAlert(_StrictPayload):
    escalation_id: UUID
    borrower_display_label: str | None = None
    hsm_state: HsmState
    detected_axes: list[OntologyAxis] = Field(default_factory=list)
    headline_vi: str
    body_vi: str
    raised_at: datetime
    require_ack: Literal[True] = True


class EscalationAcknowledged(_StrictPayload):
    escalation_id: UUID
    guardian_alias: str
    ack_latency_ms: int = Field(ge=0)
    message_vi: str


class EscalationFallback(_StrictPayload):
    escalation_id: UUID
    channel: EscalationChannel
    reason: Literal["ACK_TIMEOUT", "PUSH_EXPIRED_410", "NO_LIVE_SOCKET"]
    message_vi: str


class RescueCardDelivered(_StrictPayload):
    rescue_card_id: UUID
    escalation_id: UUID | None = None
    card_type: RescueCardType
    guardian_alias: str
    message_vi: str
    tts_script_vi: str


class AlertDigest(_StrictPayload):
    """S2-EC45: gom cảnh báo cũ > 45 phút khi người thân kết nối lại."""

    stale_alert_count: int = Field(ge=0)
    oldest_raised_at: datetime | None = None
    summary_vi: str


class ServerPing(_StrictPayload):
    server_ts_ms: int


class ServerError(_StrictPayload):
    code: str
    message_vi: str


# -------------------------------------------------------- client → server ---


class ClientAlertAck(_StrictPayload):
    escalation_id: UUID


class ClientRescueCardAck(_StrictPayload):
    rescue_card_id: UUID


class ClientPong(_StrictPayload):
    server_ts_ms: int


SERVER_FRAMES: Final[dict[str, type[BaseModel]]] = {
    "SERVER_HELLO": ServerHello,
    "EVENT_EMERGENCY_ALERT": EmergencyAlert,
    "EVENT_ESCALATION_ACKNOWLEDGED": EscalationAcknowledged,
    "EVENT_ESCALATION_FALLBACK": EscalationFallback,
    "EVENT_RESCUE_CARD": RescueCardDelivered,
    "EVENT_ALERT_DIGEST": AlertDigest,
    "SERVER_PING": ServerPing,
    "SERVER_ERROR": ServerError,
}

CLIENT_FRAMES: Final[dict[str, type[BaseModel]]] = {
    "CLIENT_ALERT_ACK": ClientAlertAck,
    "CLIENT_RESCUE_CARD_ACK": ClientRescueCardAck,
    "CLIENT_PONG": ClientPong,
}

WS_CLOSE_CODES: Final[dict[int, str]] = {
    1000: "NORMAL_CLOSURE",
    1001: "GOING_AWAY (server tắt/scale)",
    4401: "TICKET_INVALID_OR_CONSUMED",
    4403: "ROLE_FORBIDDEN",
    4408: "HEARTBEAT_TIMEOUT",
    4429: "RATE_LIMITED",
}


class WsFrame(BaseModel):
    """Phong bì chung cho mọi frame."""

    type: str
    frame_id: UUID
    sent_at: datetime
    payload: dict[str, object]


class PushPayload(_StrictPayload):
    """Payload Web Push VAPID High-Urgency (cùng nguyên tắc Zero-Amount)."""

    kind: Literal["EMERGENCY_ALERT", "RESCUE_CARD"]
    escalation_id: UUID | None = None
    rescue_card_id: UUID | None = None
    title_vi: str
    body_vi: str
    open_path: str = Field(description="vd /guardian?esc=<id>")
