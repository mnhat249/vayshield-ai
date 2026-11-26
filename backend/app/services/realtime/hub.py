"""WebSocket hub nhiều instance qua Redis Pub/Sub (File 4 §3.1 ``vs:chan:family:*``, EC-DB13).

- Mỗi instance ``PSUBSCRIBE vs:chan:family:*`` một lần (không cần subscribe/unsubscribe động) và
  chuyển tiếp tin tới các socket CỤC BỘ khớp thiết bị + đối tượng (``audience``/``link_id``).
  Người vay ở instance #1, người thân ở instance #2 vẫn nhận tức thì.
- Hiện diện (presence): ZSET ``vs:ws:presence:{device_id}`` (score = hạn, ms) để biết người thân /
  người vay có socket sống không, độc lập instance. Heartbeat làm mới hạn; socket treo nửa chừng
  (half-open, S2-EC44) tự hết hạn.
- Hàng đợi gửi mỗi socket có giới hạn; đầy hoặc gửi quá hạn thì socket bị gỡ KÈM toàn bộ frame đang
  chờ. Frame cho người vay như EVENT_ESCALATION_ACKNOWLEDGED/FALLBACK không được lưu lại: client
  PHẢI đồng bộ lại trạng thái qua REST khi nối lại (thẻ giải cứu và cảnh báo người thân thì được
  phát lại từ Redis khi nối).
- Giả định: Redis đơn (PSUBSCRIBE trên Cluster cần shard pub/sub — ghi trong báo cáo).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.logging import get_logger
from app.core.schemas.realtime import RealtimeRole

logger = get_logger("vayshield.realtime.hub")

CHANNEL_PREFIX: Final = "vs:chan:family:"
CHANNEL_PATTERN: Final = CHANNEL_PREFIX + "*"
PRESENCE_PREFIX: Final = "vs:ws:presence:"
QUEUE_MAX: Final = 64  # frame chờ gửi tối đa mỗi socket; đầy = socket chậm → gỡ
PRESENCE_TTL_FACTOR: Final = 3  # hạn hiện diện = 3 chu kỳ ping


def family_channel(device_id: UUID) -> str:
    return f"{CHANNEL_PREFIX}{device_id}"


SendFn = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(eq=False)
class LocalConnection:
    conn_id: UUID
    role: RealtimeRole
    device_id: UUID
    link_id: UUID | None
    send: SendFn
    close: Callable[[], Awaitable[None]] | None = None
    presence_member: str = field(init=False)
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=QUEUE_MAX), repr=False
    )
    writer: asyncio.Task[None] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.presence_member = f"{self.role.value}|{self.link_id or '-'}|{self.conn_id}"


class RealtimeHub:
    def __init__(
        self,
        redis: Redis,
        clock: Clock,
        *,
        ping_interval_s: float = 20,
        send_timeout_s: float = 2.0,
    ) -> None:
        self._redis = redis
        self._clock = clock
        self._ping_interval_s = ping_interval_s
        self._send_timeout_s = send_timeout_s
        self._bg: set[asyncio.Task[None]] = set()
        self._conns: dict[UUID, set[LocalConnection]] = {}
        self._pubsub: Any = None
        self._task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    # --- vòng đời --------------------------------------------------------------------------
    async def start(self) -> None:
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            pubsub = self._redis.pubsub()
            await pubsub.psubscribe(CHANNEL_PATTERN)
            # Chờ xác nhận đăng ký để tin phát ngay sau đó không bị mất (race PSUBSCRIBE).
            for _ in range(20):
                msg = await pubsub.get_message(ignore_subscribe_messages=False, timeout=0.5)
                if msg is not None and msg.get("type") == "psubscribe":
                    break
            self._pubsub = pubsub
            self._task = asyncio.create_task(self._listen(pubsub), name="realtime-hub-listener")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.punsubscribe(CHANNEL_PATTERN)
                await self._pubsub.aclose()
            self._pubsub = None

    async def _listen(self, pubsub: Any) -> None:
        while True:
            try:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("hub: pubsub lỗi, thử lại sau 1s")
                await asyncio.sleep(1.0)
                continue
            if msg is None:
                await asyncio.sleep(0)
                continue
            await self._dispatch(msg)

    async def _dispatch(self, msg: dict[str, Any]) -> None:
        channel = msg.get("channel")
        data = msg.get("data")
        if isinstance(channel, bytes):
            channel = channel.decode()
        if isinstance(data, bytes):
            data = data.decode()
        if (
            not isinstance(channel, str)
            or not isinstance(data, str)
            or not channel.startswith(CHANNEL_PREFIX)
        ):
            return
        try:
            device_id = UUID(channel[len(CHANNEL_PREFIX) :])
            envelope = json.loads(data)
            audience = RealtimeRole(envelope["audience"])
            frame = envelope["frame"]
            link = envelope.get("link_id")
            target_link = UUID(link) if link else None
        except (ValueError, KeyError, TypeError):
            logger.warning("hub: bỏ qua tin pub/sub sai định dạng")
            return
        for conn in list(self._conns.get(device_id, ())):
            if conn.role is not audience:
                continue
            if target_link is not None and conn.link_id != target_link:
                continue
            # Không await gửi trong vòng listener: socket chậm không được kẹt gia đình khác.
            try:
                conn.queue.put_nowait(frame)
            except asyncio.QueueFull:
                logger.info("hub: hàng đợi socket đầy, gỡ kết nối chậm")
                self._drop(conn)

    # --- đăng ký socket cục bộ ---------------------------------------------------------------
    def register(self, conn: LocalConnection) -> None:
        self._conns.setdefault(conn.device_id, set()).add(conn)
        conn.writer = asyncio.create_task(self._writer(conn), name=f"ws-writer-{conn.conn_id}")

    async def _writer(self, conn: LocalConnection) -> None:
        """Mỗi socket 1 task gửi riêng, có timeout: socket treo chỉ làm chính nó bị gỡ."""
        while True:
            frame = await conn.queue.get()
            try:
                async with asyncio.timeout(self._send_timeout_s):
                    await conn.send(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.info("hub: gửi tới socket lỗi/quá chậm, gỡ kết nối")
                self._drop(conn)
                return

    def _drop(self, conn: LocalConnection) -> None:
        self.unregister(conn)
        if conn.close is not None:
            task = asyncio.create_task(self._close_quietly(conn))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)

    @staticmethod
    async def _close_quietly(conn: LocalConnection) -> None:
        if conn.close is not None:
            with contextlib.suppress(Exception):
                await conn.close()

    def unregister(self, conn: LocalConnection) -> None:
        writer, conn.writer = conn.writer, None
        if writer is not None and writer is not asyncio.current_task():
            writer.cancel()
        bucket = self._conns.get(conn.device_id)
        if bucket is not None:
            bucket.discard(conn)
            if not bucket:
                self._conns.pop(conn.device_id, None)

    def local_connection_count(self) -> int:
        return sum(len(b) for b in self._conns.values())

    # --- phát tin --------------------------------------------------------------------------
    async def publish(
        self,
        device_id: UUID,
        audience: RealtimeRole,
        frame: dict[str, Any],
        *,
        link_id: UUID | None = None,
    ) -> None:
        envelope = {
            "audience": audience.value,
            "link_id": None if link_id is None else str(link_id),
            "frame": frame,
        }
        await self._redis.publish(
            family_channel(device_id), json.dumps(envelope, separators=(",", ":"))
        )

    # --- hiện diện -------------------------------------------------------------------------
    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    async def touch_presence(self, conn: LocalConnection) -> None:
        key = PRESENCE_PREFIX + str(conn.device_id)
        now = self._now_ms()
        expires = now + int(PRESENCE_TTL_FACTOR * self._ping_interval_s * 1000)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, "-inf", now)
            pipe.zadd(key, {conn.presence_member: expires})
            pipe.expire(key, 3600)
            await pipe.execute()

    async def drop_presence(self, conn: LocalConnection) -> None:
        await self._redis.zrem(PRESENCE_PREFIX + str(conn.device_id), conn.presence_member)

    async def is_live(
        self, device_id: UUID, role: RealtimeRole, *, link_id: UUID | None = None
    ) -> bool:
        members = await self._redis.zrangebyscore(
            PRESENCE_PREFIX + str(device_id), self._now_ms(), "+inf"
        )
        for raw in members:
            member = raw.decode() if isinstance(raw, bytes) else str(raw)
            m_role, m_link, _ = member.split("|", 2)
            if m_role != role.value:
                continue
            if link_id is None or m_link == str(link_id):
                return True
        return False
