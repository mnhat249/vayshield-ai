"""Vé kết nối dùng 1 lần cho WebSocket ``/ws/v1/guardian`` và SSE ``/ops/live-feed`` (EC-DB12).

- Vé = ``secrets.token_urlsafe(32)`` (43 ký tự, nằm trong 16..128 của contract), TTL 45s.
- Redis chỉ lưu ``sha256(vé)`` làm khóa → dump Redis không lộ vé còn hạn.
- ``consume`` dùng ``GETDEL`` nguyên tử: vé chỉ dùng được đúng 1 lần, kể cả khi 2 kết nối đua nhau.
- Client gọi lại ``GET /api/v1/ws-ticket`` mỗi lần reconnect (không gửi lại vé cũ).
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.errors import AppError, ErrorCode

TICKET_MIN_LEN: Final = 16
TICKET_MAX_LEN: Final = 128


class TicketNamespace(StrEnum):
    WS = "vs:ws:ticket:"
    OPS_STREAM = "vs:ops:ticket:"


@dataclass(frozen=True, slots=True)
class IssuedTicket:
    ticket: str
    expires_at: datetime
    ttl_seconds: int

    def __repr__(self) -> str:  # không lộ vé
        return f"IssuedTicket(expires_at={self.expires_at.isoformat()}, ttl={self.ttl_seconds})"


@dataclass(frozen=True, slots=True)
class TicketClaims:
    role: str
    subject: str


def _key(namespace: TicketNamespace, ticket: str) -> str:
    return namespace.value + hashlib.sha256(ticket.encode("utf-8")).hexdigest()


async def issue_ticket(
    redis: Redis,
    clock: Clock,
    *,
    namespace: TicketNamespace,
    role: str,
    subject: str,
    ttl_s: int,
) -> IssuedTicket:
    if ttl_s < 1:
        raise ValueError("ttl_s phải >= 1")
    ticket = secrets.token_urlsafe(32)
    claims = json.dumps({"role": role, "subject": subject}, separators=(",", ":"))
    created = await redis.set(_key(namespace, ticket), claims, ex=ttl_s, nx=True)
    if not created:  # gần như không thể (256-bit), nhưng không bao giờ ghi đè vé khác
        raise AppError(ErrorCode.INTERNAL_ERROR, 500)
    return IssuedTicket(
        ticket=ticket, expires_at=clock.now() + timedelta(seconds=ttl_s), ttl_seconds=ttl_s
    )


async def consume_ticket(
    redis: Redis, ticket: str | None, *, namespace: TicketNamespace
) -> TicketClaims:
    """Xóa nguyên tử và trả claims; vé sai/hết hạn/đã dùng → ``TICKET_INVALID_OR_CONSUMED`` 401."""
    invalid = AppError(ErrorCode.TICKET_INVALID_OR_CONSUMED, 401)
    if not ticket or not (TICKET_MIN_LEN <= len(ticket) <= TICKET_MAX_LEN):
        raise invalid
    raw = await redis.getdel(_key(namespace, ticket))
    if raw is None:
        raise invalid
    try:
        data = json.loads(raw)
        return TicketClaims(role=str(data["role"]), subject=str(data["subject"]))
    except (ValueError, KeyError, TypeError):
        raise invalid from None
