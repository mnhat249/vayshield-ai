"""SSE Live Threat Feed của Fraud Ops: Redis pub/sub -> khung ``ops.threat_signal``.

Nơi phát hiện đe dọa (T14 khi đạt quorum, webhook B2B...) gọi ``publish_threat_signal``. Tín hiệu
chỉ chứa STK ĐÃ CHE + tóm tắt do mẫu cứng sinh (OpsThreatSignal không có trường số thô).
Kết nối bị giới hạn ``MAX_STREAM_S``: hết hạn thì đóng, client lấy vé mới (JWT không bị giữ vô hạn).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Final

from pydantic import ValidationError
from redis.asyncio import Redis

from app.core.schemas.sse import OpsThreatSignal
from app.core.sse import format_sse

THREAT_CHANNEL: Final = "vs:ops:threat-signals"
MAX_STREAM_S: Final = 30 * 60
_POLL_S: Final = 0.5


async def publish_threat_signal(redis: Redis, signal: OpsThreatSignal) -> int:
    """Trả số subscriber nhận được."""
    return int(await redis.publish(THREAT_CHANNEL, signal.model_dump_json()))


async def threat_signal_frames(
    redis: Redis, *, max_seconds: float = MAX_STREAM_S, max_events: int | None = None
) -> AsyncIterator[str]:
    pubsub = redis.pubsub()
    await pubsub.subscribe(THREAT_CHANNEL)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max_seconds
    sent = 0
    try:
        while loop.time() < deadline and (max_events is None or sent < max_events):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=_POLL_S)
            if message is None:
                await asyncio.sleep(0)
                continue
            try:
                signal = OpsThreatSignal.model_validate_json(message["data"])
            except (ValidationError, ValueError, TypeError):
                continue  # tín hiệu hỏng: bỏ qua, không làm sập luồng
            sent += 1
            yield format_sse("ops.threat_signal", signal, event_id=str(signal.signal_id))
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(THREAT_CHANNEL)
            await pubsub.aclose()
