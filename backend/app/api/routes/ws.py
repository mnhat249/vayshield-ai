"""STUB M0 — Vé WebSocket 1 lần + hub ``/ws/v1/guardian``. Logic thật: T12.

Stub WS: chấp nhận mọi ticket không rỗng, gửi SERVER_HELLO rồi 1 EVENT_EMERGENCY_ALERT mẫu,
nhận CLIENT_ALERT_ACK → trả EVENT_ESCALATION_ACKNOWLEDGED. Đủ để Nhóm 2 dựng UI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.core import mocks
from app.core.auth_headers import RealtimeCaller
from app.core.ids import new_uuid7
from app.core.openapi import WS_GUARDIAN_PATH, error_responses
from app.core.schemas.realtime import RealtimeRole, WsTicketResponse
from app.core.stubs import stub

router = APIRouter(tags=["realtime"])


def _frame(frame_type: str, payload: BaseModel) -> dict[str, Any]:
    return {
        "type": frame_type,
        "frame_id": str(new_uuid7()),
        "sent_at": datetime.now(UTC).isoformat(),
        "payload": payload.model_dump(mode="json"),
    }


@router.get(
    "/api/v1/ws-ticket",
    operation_id="wsTicket",
    response_model=WsTicketResponse,
    responses=error_responses(401),
    summary="Cấp vé WebSocket 1 lần (TTL 45s) — gọi lại mỗi lần reconnect (EC-DB12)",
)
@stub
async def issue_ws_ticket(caller: RealtimeCaller) -> WsTicketResponse:
    role = RealtimeRole.GUARDIAN if caller.guardian_token else RealtimeRole.BORROWER
    return mocks.WS_TICKET.model_copy(update={"role": role})


@router.websocket(WS_GUARDIAN_PATH)
@stub
async def guardian_ws(
    websocket: WebSocket, ticket: Annotated[str, Query(min_length=1, max_length=128)]
) -> None:
    await websocket.accept()
    await websocket.send_json(_frame("SERVER_HELLO", mocks.WS_SERVER_FRAMES["SERVER_HELLO"]))
    await websocket.send_json(
        _frame("EVENT_EMERGENCY_ALERT", mocks.WS_SERVER_FRAMES["EVENT_EMERGENCY_ALERT"])
    )
    try:
        while True:
            message = await websocket.receive_json()
            if isinstance(message, dict) and message.get("type") == "CLIENT_ALERT_ACK":
                await websocket.send_json(
                    _frame(
                        "EVENT_ESCALATION_ACKNOWLEDGED",
                        mocks.WS_SERVER_FRAMES["EVENT_ESCALATION_ACKNOWLEDGED"],
                    )
                )
    except WebSocketDisconnect:
        return
