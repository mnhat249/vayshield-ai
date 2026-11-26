# ruff: noqa: E501  (thông điệp tiếng Việt dài)
"""Dựng frame WebSocket + nội dung cảnh báo Zero-Amount (contracts/ws-protocol.json).

Zero-Amount Privacy Envelope: nội dung gửi tới người thân (WS, Push, SMS) được dựng CHỈ từ danh
sách trắng (lý do, trạng thái HSM, trục ontology, nhãn người vay). Không bao giờ có số tiền, tên
công ty vay hay số tài khoản — kể cả khi payload gốc của thiết bị có thêm trường (đã bị
``extra=forbid`` chặn 422).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final
from uuid import UUID

from pydantic import BaseModel

from app.core.ids import new_uuid7
from app.core.schemas.common import HsmState, OntologyAxis
from app.core.schemas.family import RescueCardType
from app.core.schemas.realtime import EmergencyAlert, PushPayload
from app.core.schemas.shield import EscalationReason

DEFAULT_LABEL_VI: Final = "Người thân của bạn"

_REASON_BODY_VI: Final[dict[EscalationReason, str]] = {
    EscalationReason.COERCION_DETECTED: (
        "Có dấu hiệu {who} đang bị ép buộc hoặc làm theo hướng dẫn của kẻ lạ qua điện thoại. "
        "Hãy gọi ngay và bảo {who} dừng mọi giao dịch."
    ),
    EscalationReason.SMURF_BYPASS_ATTEMPT: (
        "{who} đang cố chia nhỏ giao dịch để vượt qua cảnh báo. "
        "Hãy gọi ngay và hỏi {who} đang làm gì."
    ),
    EscalationReason.USER_CONFIRMED_SCAM: (
        "{who} vừa xác nhận có thể đang bị lừa đảo. Hãy gọi ngay để hỗ trợ."
    ),
    EscalationReason.USER_ALREADY_TRANSFERRED: (
        "{who} báo đã lỡ chuyển tiền cho người lạ. Hãy gọi ngay và hỗ trợ báo ngân hàng "
        "trong Giờ Vàng."
    ),
}

HEADLINE_VI: Final = "Khẩn cấp: người thân có thể đang bị lừa chuyển tiền"

RESCUE_TTS_VI: Final[dict[RescueCardType, str]] = {
    RescueCardType.CALL_ME_NOW: "Người thân đang gọi cho bạn. Hãy nghe máy ngay.",
    RescueCardType.STOP_TRANSFER_NOW: "Người thân yêu cầu bạn dừng ngay mọi giao dịch chuyển tiền.",
    RescueCardType.I_AM_COMING: "Người thân đang trên đường tới chỗ bạn. Hãy chờ và đừng chuyển tiền.",
    RescueCardType.CALL_BANK_HOTLINE: "Hãy gọi ngay hotline chính thức của ngân hàng để được hỗ trợ.",
}
RESCUE_DEFAULT_MESSAGE_VI: Final[dict[RescueCardType, str]] = {
    RescueCardType.CALL_ME_NOW: "Hãy nghe máy, người thân đang gọi cho bạn.",
    RescueCardType.STOP_TRANSFER_NOW: "Dừng lại, đừng chuyển tiền! Người thân sẽ gọi ngay.",
    RescueCardType.I_AM_COMING: "Người thân đang trên đường tới. Hãy chờ, đừng chuyển tiền.",
    RescueCardType.CALL_BANK_HOTLINE: "Hãy gọi hotline chính thức của ngân hàng ngay.",
}


def who(label: str | None) -> str:
    return label.strip() if label and label.strip() else DEFAULT_LABEL_VI


def make_frame(
    frame_type: str, payload: BaseModel | dict[str, Any], sent_at: datetime
) -> dict[str, Any]:
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    return {
        "type": frame_type,
        "frame_id": str(new_uuid7()),
        "sent_at": sent_at.isoformat(),
        "payload": body,
    }


def build_emergency_alert(
    *,
    escalation_id: UUID,
    reason: EscalationReason,
    hsm_state: HsmState,
    axes: list[OntologyAxis],
    label: str | None,
    raised_at: datetime,
) -> EmergencyAlert:
    return EmergencyAlert(
        escalation_id=escalation_id,
        borrower_display_label=label,
        hsm_state=hsm_state,
        detected_axes=axes,
        headline_vi=HEADLINE_VI,
        body_vi=_REASON_BODY_VI[reason].format(who=who(label)),
        raised_at=raised_at,
    )


def build_push_payload(alert: EmergencyAlert) -> PushPayload:
    return PushPayload(
        kind="EMERGENCY_ALERT",
        escalation_id=alert.escalation_id,
        title_vi=alert.headline_vi,
        body_vi=alert.body_vi,
        open_path=f"/guardian?esc={alert.escalation_id}",
    )


def build_sms_text(alert: EmergencyAlert) -> str:
    return f"VayShield: {alert.body_vi}"
