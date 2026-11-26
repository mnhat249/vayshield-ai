"""Vé WebSocket 1 lần + hub ``/ws/v1/guardian`` (File 4 §4.1, §4.2(C), EC-DB12/13). Logic: T12.

- Vé sai/hết hạn/đã dùng: server LUÔN accept rồi gửi ``SERVER_ERROR`` và đóng 4401 (contract).
- Ping 20s; không nhận được bất kỳ frame nào qua ``MAX_MISSED_PINGS`` chu kỳ → đóng 4408 (socket
  half-open, S2-EC44; hiện diện Redis cũng tự hết hạn nên SMS dự phòng vẫn chạy theo ACK timeout).
- ``CLIENT_ALERT_ACK`` chỉ hợp lệ với vai GUARDIAN (khác → 4403).
- Quá ``MAX_FRAMES_PER_WINDOW`` frame/10s → đóng 4429.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any, Final
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.openapi import WS_GUARDIAN_PATH, error_responses
from app.core.schemas.realtime import (
    ClientAlertAck,
    ClientRescueCardAck,
    RealtimeRole,
    ServerError,
    ServerHello,
    ServerPing,
    WsTicketResponse,
)
from app.security.rate_limit import client_ip
from app.security.tickets import TicketNamespace, consume_ticket, issue_ticket
from app.services.realtime.container import RealtimeIdentity, RealtimeServices, Services
from app.services.realtime.frames import make_frame
from app.services.realtime.hub import LocalConnection
from app.services.realtime.models import GuardianLink, LinkStatus

logger = get_logger("vayshield.realtime.ws")

WS_CLOSE_TICKET_INVALID: Final = 4401
WS_CLOSE_ROLE_FORBIDDEN: Final = 4403
WS_CLOSE_GOING_AWAY: Final = 1001
WS_CLOSE_HEARTBEAT_TIMEOUT: Final = 4408
WS_CLOSE_RATE_LIMITED: Final = 4429
MAX_MISSED_PINGS: Final = 2
MAX_FRAMES_PER_WINDOW: Final = 30
FRAME_WINDOW_S: Final = 10.0

router = APIRouter(tags=["realtime"])


@router.get(
    "/api/v1/ws-ticket",
    operation_id="wsTicket",
    response_model=WsTicketResponse,
    responses=error_responses(401),
    summary="Cấp vé WebSocket 1 lần (TTL 45s) — gọi lại mỗi lần reconnect (EC-DB12)",
)
async def issue_ws_ticket(identity: RealtimeIdentity, services: Services) -> WsTicketResponse:
    if isinstance(identity, GuardianLink):
        role, subject = RealtimeRole.GUARDIAN, str(identity.link_id)
    else:
        role, subject = RealtimeRole.BORROWER, str(identity.device_id)
    ttl = services.settings.ws_ticket_ttl_s
    issued = await issue_ticket(
        services.redis,
        services.clock,
        namespace=TicketNamespace.WS,
        role=role.value,
        subject=subject,
        ttl_s=ttl,
    )
    return WsTicketResponse(
        ticket=issued.ticket,
        role=role,
        expires_at=issued.expires_at,
        ttl_seconds=issued.ttl_seconds,
        ws_path=WS_GUARDIAN_PATH,
    )


class _Sender:
    """Tuần tự hóa gửi frame (hub và vòng chính cùng gửi lên 1 socket)."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._lock = asyncio.Lock()

    async def __call__(self, frame: dict[str, Any]) -> None:
        async with self._lock:
            await self._ws.send_json(frame)


async def _reject(websocket: WebSocket, services: RealtimeServices, code: int, error: str) -> None:
    payload = ServerError(code=error, message_vi=_ERRORS_VI.get(error, "Kết nối bị từ chối."))
    try:
        await websocket.send_json(make_frame("SERVER_ERROR", payload, services.clock.now()))
    finally:
        await websocket.close(code=code)


_ERRORS_VI: Final = {
    "TICKET_INVALID_OR_CONSUMED": "Vé kết nối không hợp lệ hoặc đã được dùng. Hãy lấy vé mới.",
    "RATE_LIMITED": "Bạn kết nối quá nhanh. Vui lòng thử lại sau.",
    "LINK_REVOKED": "Liên kết người thân đã bị hủy.",
    "ROLE_FORBIDDEN": "Vai trò của bạn không được gửi frame này.",
    "HEARTBEAT_TIMEOUT": "Mất tín hiệu kết nối.",
    "INVALID_FRAME": "Khung tin không hợp lệ.",
    "UNKNOWN_FRAME": "Loại khung tin không được hỗ trợ.",
    "GUARDIAN_PUSH_EXPIRED": "Thông báo đẩy của người thân đã hết hạn. Hãy ghép nối lại khi tiện.",
}


async def _resolve_identity(
    services: RealtimeServices, ticket: str | None
) -> tuple[RealtimeRole, UUID, GuardianLink | None] | None:
    """Tiêu thụ vé (GETDEL nguyên tử). ``None`` nếu vé sai hoặc liên kết không còn hiệu lực."""
    try:
        claims = await consume_ticket(services.redis, ticket, namespace=TicketNamespace.WS)
        role = RealtimeRole(claims.role)
        subject = UUID(claims.subject)
    except (AppError, ValueError):
        return None
    if role is RealtimeRole.BORROWER:
        return role, subject, None
    link = await services.repo.get_link(subject)
    if link is None or link.status not in (LinkStatus.ACTIVE, LinkStatus.PUSH_EXPIRED_410):
        return None
    return role, link.device_id, link


@router.websocket(WS_GUARDIAN_PATH)
async def guardian_ws(
    websocket: WebSocket, services: Services, ticket: Annotated[str | None, Query()] = None
) -> None:
    await websocket.accept()
    ip = client_ip((websocket.client.host, websocket.client.port) if websocket.client else None)
    try:
        await services.limiter.enforce(device_id=None, ip="ws:" + ip, clock=services.clock)
    except AppError:
        await _reject(websocket, services, WS_CLOSE_RATE_LIMITED, "RATE_LIMITED")
        return
    resolved = await _resolve_identity(services, ticket)
    if resolved is None:
        await _reject(websocket, services, WS_CLOSE_TICKET_INVALID, "TICKET_INVALID_OR_CONSUMED")
        return
    role, device_id, link = resolved

    send = _Sender(websocket)
    conn = LocalConnection(
        conn_id=uuid4(),
        role=role,
        device_id=device_id,
        link_id=None if link is None else link.link_id,
        send=send,
        close=lambda: websocket.close(code=WS_CLOSE_GOING_AWAY),
    )
    hub = services.hub
    await hub.start()
    hub.register(conn)
    try:
        await hub.touch_presence(conn)
        hello = ServerHello(
            connection_id=conn.conn_id,
            role=role,
            heartbeat_interval_s=max(1, int(services.ws_ping_interval_s)),
            ack_timeout_s=services.ack_timeout_s,
        )
        await send(make_frame("SERVER_HELLO", hello, services.clock.now()))
        await _flush_backlog(services, conn, link, send)
        await _receive_loop(websocket, services, conn, link, send)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(conn)
        try:
            await hub.drop_presence(conn)
        except Exception:
            logger.info("ws: không gỡ được presence (sẽ tự hết hạn)")


async def _flush_backlog(
    services: RealtimeServices, conn: LocalConnection, link: GuardianLink | None, send: _Sender
) -> None:
    if link is not None:  # người thân: cảnh báo chưa ACK + bản tin tóm tắt cũ > 45 phút (S2-EC45)
        for frame in await services.escalation.pending_frames_for(link):
            await send(frame)
        return
    for frame in await services.rescue.drain_queue(conn.device_id):  # người vay: thẻ giải cứu chờ
        await send(frame)
    if any(
        lk.status is LinkStatus.PUSH_EXPIRED_410
        for lk in await services.repo.list_live_for_device(conn.device_id)
    ):  # EC-DB14: nhắc ghép nối lại
        err = ServerError(
            code="GUARDIAN_PUSH_EXPIRED", message_vi=_ERRORS_VI["GUARDIAN_PUSH_EXPIRED"]
        )
        await send(make_frame("SERVER_ERROR", err, services.clock.now()))


async def _receive_loop(
    websocket: WebSocket,
    services: RealtimeServices,
    conn: LocalConnection,
    link: GuardianLink | None,
    send: _Sender,
) -> None:
    interval = float(services.ws_ping_interval_s)
    missed = 0
    window_start = services.clock.monotonic()
    count = 0
    loop = asyncio.get_running_loop()
    last_link_check = loop.time()
    while True:
        if link is not None and loop.time() - last_link_check >= interval:
            # Liên kết bị hủy khi socket đang mở: không để tiếp tục nhận cảnh báo.
            last_link_check = loop.time()
            current = await services.repo.get_link(link.link_id)
            if current is None or current.status not in (
                LinkStatus.ACTIVE,
                LinkStatus.PUSH_EXPIRED_410,
            ):
                await _reject(websocket, services, WS_CLOSE_TICKET_INVALID, "LINK_REVOKED")
                return
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=interval)
        except TimeoutError:
            missed += 1
            if missed > MAX_MISSED_PINGS:
                await _reject(websocket, services, WS_CLOSE_HEARTBEAT_TIMEOUT, "HEARTBEAT_TIMEOUT")
                return
            ping = ServerPing(server_ts_ms=int(services.clock.now().timestamp() * 1000))
            await send(make_frame("SERVER_PING", ping, services.clock.now()))
            continue
        missed = 0
        now = services.clock.monotonic()
        if now - window_start > FRAME_WINDOW_S:
            window_start, count = now, 0
        count += 1
        if count > MAX_FRAMES_PER_WINDOW:
            await _reject(websocket, services, WS_CLOSE_RATE_LIMITED, "RATE_LIMITED")
            return
        await hub_touch(services, conn)
        if not await _handle_frame(websocket, services, conn, link, send, raw):
            return


async def hub_touch(services: RealtimeServices, conn: LocalConnection) -> None:
    try:
        await services.hub.touch_presence(conn)
    except Exception:
        logger.info("ws: không làm mới được presence")


async def _handle_frame(
    websocket: WebSocket,
    services: RealtimeServices,
    conn: LocalConnection,
    link: GuardianLink | None,
    send: _Sender,
    raw: str,
) -> bool:
    """``False`` = đã đóng socket."""
    try:
        msg = json.loads(raw)
        frame_type = msg["type"]
        payload = msg.get("payload", {})
        if not isinstance(frame_type, str) or not isinstance(payload, dict):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        await _send_error(services, send, "INVALID_FRAME")
        return True
    try:
        if frame_type == "CLIENT_PONG":
            return True
        if frame_type == "CLIENT_ALERT_ACK":
            if link is None:
                await _reject(websocket, services, WS_CLOSE_ROLE_FORBIDDEN, "ROLE_FORBIDDEN")
                return False
            ack = ClientAlertAck.model_validate(payload)
            await services.escalation.acknowledge(link, ack.escalation_id)
            return True
        if frame_type == "CLIENT_RESCUE_CARD_ACK":
            if link is not None:  # chỉ người VAY được ACK thẻ giải cứu
                await _reject(websocket, services, WS_CLOSE_ROLE_FORBIDDEN, "ROLE_FORBIDDEN")
                return False
            rc_ack = ClientRescueCardAck.model_validate(payload)
            await services.rescue.ack(conn.device_id, rc_ack.rescue_card_id)
            return True
    except ValidationError:
        await _send_error(services, send, "INVALID_FRAME")
        return True
    await _send_error(services, send, "UNKNOWN_FRAME")
    return True


async def _send_error(services: RealtimeServices, send: _Sender, code: str) -> None:
    err = ServerError(code=code, message_vi=_ERRORS_VI[code])
    await send(make_frame("SERVER_ERROR", err, services.clock.now()))
