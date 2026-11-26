"""Thẻ Giải cứu 2 chiều: người thân → máy người vay (WS reverse push < 150ms, File 4 §4.1).

Đường nóng chỉ gồm: kiểm tra liên kết/leo thang (1-2 lệnh Redis), 1 lệnh PUBLISH và 1 lệnh kiểm tra
hiện diện — không gọi PostgreSQL hay dịch vụ ngoài. Người vay không có socket sống → xếp hàng
``vs:rescue:queue:{device_id}`` (tối đa 20 thẻ, 24h) và phát khi họ kết nối lại.
"""

from __future__ import annotations

import json
from typing import Any, Final
from uuid import UUID

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_uuid7
from app.core.schemas.family import SendRescueCardRequest, SendRescueCardResponse
from app.core.schemas.realtime import RealtimeRole, RescueCardDelivered
from app.core.schemas.shield import EscalationChannel
from app.services.realtime.escalation import owner_key
from app.services.realtime.frames import RESCUE_DEFAULT_MESSAGE_VI, RESCUE_TTS_VI, make_frame
from app.services.realtime.hub import RealtimeHub
from app.services.realtime.models import GuardianLink

QUEUE_MAX: Final = 20
QUEUE_TTL_S: Final = 24 * 3600


def rescue_queue_key(device_id: UUID) -> str:
    return f"vs:rescue:queue:{device_id}"


class RescueService:
    def __init__(self, *, redis: Redis, clock: Clock, hub: RealtimeHub) -> None:
        self._redis = redis
        self._clock = clock
        self._hub = hub

    async def send(self, link: GuardianLink, body: SendRescueCardRequest) -> SendRescueCardResponse:
        device_id = link.device_id
        if body.escalation_id is not None:
            raw = await self._redis.get(owner_key(body.escalation_id))
            text = raw.decode() if isinstance(raw, bytes) else raw
            try:
                owner = UUID(json.loads(text)["device_id"]) if text else None
            except (ValueError, KeyError, TypeError):
                owner = None
            if owner != device_id:
                raise AppError(ErrorCode.NOT_FOUND, 404)
        now = self._clock.now()
        message = (body.message_vi or "").strip() or RESCUE_DEFAULT_MESSAGE_VI[body.card_type]
        card_id = new_uuid7()
        payload = RescueCardDelivered(
            rescue_card_id=card_id,
            escalation_id=body.escalation_id,
            card_type=body.card_type,
            guardian_alias=link.guardian_alias,
            message_vi=message,
            tts_script_vi=RESCUE_TTS_VI[body.card_type],
        )
        frame = make_frame("EVENT_RESCUE_CARD", payload, now)
        # Luôn lưu trước (ZSET theo thời gian); chỉ xóa khi người vay gửi CLIENT_RESCUE_CARD_ACK.
        # Socket "sống" nhưng nửa chết vẫn không làm mất thẻ.
        key = rescue_queue_key(device_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zadd(key, {json.dumps(frame, separators=(",", ":")): int(now.timestamp() * 1000)})
            pipe.zremrangebyrank(key, 0, -(QUEUE_MAX + 1))
            pipe.expire(key, QUEUE_TTL_S)
            await pipe.execute()
        live = await self._hub.is_live(device_id, RealtimeRole.BORROWER)
        if live:
            await self._hub.publish(device_id, RealtimeRole.BORROWER, frame)
            return SendRescueCardResponse(
                rescue_card_id=card_id,
                delivered_via=[EscalationChannel.WEBSOCKET],
                queued=False,
                sent_at=now,
            )
        return SendRescueCardResponse(
            rescue_card_id=card_id, delivered_via=[], queued=True, sent_at=now
        )

    async def drain_queue(self, device_id: UUID) -> list[dict[str, Any]]:
        """Thẻ chưa được ACK (phát lại khi người vay kết nối; KHÔNG xóa ở đây)."""
        items: list[Any] = await self._redis.zrange(rescue_queue_key(device_id), 0, -1)
        out: list[dict[str, Any]] = []
        for raw in items:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out

    async def ack(self, device_id: UUID, rescue_card_id: UUID) -> None:
        key = rescue_queue_key(device_id)
        members: list[Any] = await self._redis.zrange(key, 0, -1)
        for raw in members:
            try:
                if json.loads(raw)["payload"]["rescue_card_id"] == str(rescue_card_id):
                    await self._redis.zrem(key, raw)
            except (ValueError, KeyError, TypeError):
                continue
