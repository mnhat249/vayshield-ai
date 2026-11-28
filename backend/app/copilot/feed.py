"""SSE Live Threat Feed của Fraud Ops: Redis pub/sub -> khung ``ops.threat_signal``.

Nơi phát hiện đe dọa (T14 khi đạt quorum, webhook B2B...) gọi ``publish_threat_signal``. Tín hiệu
chỉ chứa STK ĐÃ CHE + tóm tắt do mẫu cứng sinh (OpsThreatSignal không có trường số thô).
Kết nối bị giới hạn ``min(MAX_STREAM_S, TTL access token)``: hết hạn thì đóng, client lấy vé mới
(luồng không sống lâu hơn JWT đã dùng để xin vé).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Final

from pydantic import ValidationError
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.schemas.sse import OpsThreatSignal
from app.core.sse import format_sse

THREAT_CHANNEL: Final = "vs:ops:threat-signals"
MAX_STREAM_S: Final = 30 * 60
_POLL_S: Final = 0.5


def stream_limit_s() -> float:
    """Thời lượng tối đa một kết nối SSE: không vượt TTL của access token Ops."""
    return float(min(MAX_STREAM_S, get_settings().jwt_ops_access_ttl_s))


async def publish_threat_signal(redis: Redis, signal: OpsThreatSignal) -> int:
    """Trả số subscriber nhận được."""
    return int(await redis.publish(THREAT_CHANNEL, signal.model_dump_json()))


async def threat_signal_frames(
    redis: Redis, *, max_seconds: float | None = None, max_events: int | None = None
) -> AsyncIterator[str]:
    if max_seconds is None:
        max_seconds = stream_limit_s()
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
