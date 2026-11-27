# ruff: noqa: E501  (thông điệp tiếng Việt dài)
"""Chuỗi leo thang WS → Web Push → SMS/Email có xác nhận đã đọc (File 4 §4.2(C), S2-EC44/EC45).

Trạng thái ở Redis (không giữ trong RAM) nên ACK có thể đến BẤT KỲ instance nào:

- ``vs:ack:timer:{escalation_id}`` HASH (TTL 5 phút): ``status`` PENDING → ACKNOWLEDGED | FALLBACK,
  chuyển bằng script Lua nguyên tử (đua giữa ACK và hết 10 giây: đúng 1 bên thắng).
- ``vs:alerts:pending:{device_id}`` HASH: cảnh báo chưa ACK theo (escalation, link) để phát lại khi
  người thân kết nối lại; cảnh báo > 45 phút gom thành 1 ``EVENT_ALERT_DIGEST`` im lặng (S2-EC45).
- ``vs:esc:owner:{escalation_id}``: chủ sở hữu thiết bị + thời điểm phát (72h).

Hẹn giờ 10 giây chạy bằng ``Sleeper`` tiêm vào (test dùng đồng hồ giả). Giả định chấp nhận: hẹn giờ
là asyncio task ở instance nhận ``/escalate``; instance đó sập trong 10 giây thì SMS không tự bắn
(xem rủi ro trong báo cáo). Cảnh báo luôn còn trong ``vs:alerts:pending`` để người thân thấy khi
mở lại PWA.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID

from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_uuid7
from app.core.logging import get_logger
from app.core.schemas.realtime import (
    AlertDigest,
    EmergencyAlert,
    EscalationAcknowledged,
    EscalationFallback,
    RealtimeRole,
)
from app.core.schemas.shield import (
    EscalateRequest,
    EscalateResponse,
    EscalationChannel,
)
from app.services.realtime.frames import (
    build_emergency_alert,
    build_push_payload,
    build_sms_text,
    make_frame,
)
from app.services.realtime.hub import RealtimeHub
from app.services.realtime.models import GuardianLink
from app.services.realtime.repository import FamilyRepository
from app.services.realtime.senders import PushResult, PushSender, SmsSender

logger = get_logger("vayshield.realtime.escalation")

Sleeper = Callable[[float], Awaitable[None]]

ACK_TIMER_TTL_S: Final = 300
PENDING_TTL_S: Final = 72 * 3600
OWNER_TTL_S: Final = 72 * 3600
IDEM_TTL_S: Final = 600
STALE_AFTER_S: Final = 45 * 60  # S2-EC45

ACK_DUE_KEY: Final = "vs:ack:due"
SWEEP_BATCH: Final = 50
STATUS_PENDING: Final = "PENDING"
STATUS_ACK: Final = "ACKNOWLEDGED"
STATUS_FALLBACK: Final = "FALLBACK"

# KEYS[1]=hash; ARGV = các trạng thái nguồn hợp lệ..., trạng thái đích (cuối).
# Trả: 1 đã chuyển, 0 trạng thái hiện tại không nằm trong nguồn, -1 hash không tồn tại.
_TRANSITION_LUA: Final = """
local cur = redis.call('HGET', KEYS[1], 'status')
if not cur then return -1 end
local n = #ARGV
for i = 1, n - 1 do
  if cur == ARGV[i] then
    redis.call('HSET', KEYS[1], 'status', ARGV[n])
    return 1
  end
end
return 0
"""


# KEYS[1]=ZSET hạn; ARGV: now_ms, batch, lease_ms. Claim = đẩy điểm số ra sau (lease); chỉ
# ZREM khi xử lý xong từng mã. Lỗi giữa chừng → lease hết hạn thì mã tự được thử lại.
_CLAIM_DUE_LUA: Final = """
local ids = redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', ARGV[1], 'LIMIT', 0, tonumber(ARGV[2]))
for _, id in ipairs(ids) do
  redis.call('ZADD', KEYS[1], tonumber(ARGV[1]) + tonumber(ARGV[3]), id)
end
return ids
"""
LEASE_MS: Final = 30_000
SEND_LEASE_MS: Final = 60_000  # hạn "đang gửi" của 1 link; > timeout nhà cung cấp SMS
MAX_SEND_TRIES: Final = 3

# Pha 1: giành quyền gửi cho 1 link. KEYS[1]=hash timer. ARGV: link, now_ms, lease_ms, max_tries,
# ttl_s. Trả 1 được gửi | 0 đã xong | -1 hash đã hết hạn | -2 hết số lần thử | -3 đang có bên gửi.
# Chỉ ghi khi hash còn tồn tại (không tạo key không TTL); mỗi lần claim gia hạn TTL hash.
_CLAIM_SEND_LUA: Final = """
if redis.call('EXISTS', KEYS[1]) == 0 then return -1 end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
local l = ARGV[1]
if redis.call('HEXISTS', KEYS[1], 'done:' .. l) == 1 then return 0 end
local s = redis.call('HGET', KEYS[1], 'sending:' .. l)
if s and tonumber(s) > tonumber(ARGV[2]) then return -3 end
local t = redis.call('HINCRBY', KEYS[1], 'tries:' .. l, 1)
if t > tonumber(ARGV[4]) then
  redis.call('HSET', KEYS[1], 'done:' .. l, 'exhausted')
  redis.call('HDEL', KEYS[1], 'sending:' .. l)
  return -2
end
redis.call('HSET', KEYS[1], 'sending:' .. l, tonumber(ARGV[2]) + tonumber(ARGV[3]))
return 1
"""
# Pha 2: kết thúc. ARGV: link, ok('1'/'0'). done CHỈ ghi khi gửi thành công.
_FINISH_SEND_LUA: Final = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
redis.call('HDEL', KEYS[1], 'sending:' .. ARGV[1])
if ARGV[2] == '1' then redis.call('HSET', KEYS[1], 'done:' .. ARGV[1], '1') end
return 1
"""


def ack_timer_key(escalation_id: UUID) -> str:
    return f"vs:ack:timer:{escalation_id}"


def pending_alerts_key(device_id: UUID) -> str:
    return f"vs:alerts:pending:{device_id}"


def owner_key(escalation_id: UUID) -> str:
    return f"vs:esc:owner:{escalation_id}"


def _idem_key(device_id: UUID, client_event_id: str) -> str:
    digest = hashlib.sha256(client_event_id.encode()).hexdigest()
    return f"vs:esc:idem:{device_id}:{digest}"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class EscalationService:
    def __init__(
        self,
        *,
        redis: Redis,
        clock: Clock,
        repo: FamilyRepository,
        hub: RealtimeHub,
        push: PushSender,
        sms: SmsSender,
        sleeper: Sleeper = asyncio.sleep,
        ack_timeout_s: int = 10,
        stale_after_s: int = STALE_AFTER_S,
        sweep_interval_s: float | None = 2.0,
    ) -> None:
        self._redis = redis
        self._clock = clock
        self._repo = repo
        self._hub = hub
        self._push = push
        self._sms = sms
        self._sleep = sleeper
        self.ack_timeout_s = ack_timeout_s
        self._stale_after = timedelta(seconds=stale_after_s)
        self._transition_script = redis.register_script(_TRANSITION_LUA)
        self._claim_due = redis.register_script(_CLAIM_DUE_LUA)
        self._claim_send = redis.register_script(_CLAIM_SEND_LUA)
        self._finish_send = redis.register_script(_FINISH_SEND_LUA)
        self._watchers: dict[UUID, asyncio.Task[None]] = {}
        self._sweep_interval_s = sweep_interval_s
        self._sweeper: asyncio.Task[None] | None = None

    def ensure_sweeper(self) -> None:
        """Vòng quét hạn ACK chạy trên MỌI instance (lưới an toàn khi pod chứa hẹn giờ cục bộ sập).
        Gọi lười khi có traffic; lifespan nên gọi ``start`` (xem báo cáo)."""
        if self._sweep_interval_s is None:
            return
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop(), name="ack-sweeper")

    async def start(self) -> None:
        self.ensure_sweeper()

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await self.sweep_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ack_sweep_failed")
            await self._sleep(self._sweep_interval_s or 2.0)

    async def aclose(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._sweeper
            self._sweeper = None
        tasks = list(self._watchers.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._watchers.clear()

    async def wait_idle(self) -> None:
        """Chờ mọi hẹn giờ đang chạy xong (dùng trong test)."""
        while self._watchers:
            await asyncio.gather(*list(self._watchers.values()), return_exceptions=True)

    # --- /shield/escalate ------------------------------------------------------------------
    async def escalate(self, device_id: UUID, req: EscalateRequest) -> EscalateResponse:
        idem = _idem_key(device_id, req.client_event_id)
        claimed = await self._redis.set(idem, "pending", nx=True, ex=IDEM_TTL_S)
        if not claimed:
            cached = await self._redis.get(idem)
            text = cached.decode() if isinstance(cached, bytes) else cached
            if text and text != "pending":
                return EscalateResponse.model_validate_json(text)
            raise AppError(ErrorCode.CONFLICT, 409, "Yêu cầu leo thang đang được xử lý.")
        try:
            resp = await self._escalate_new(device_id, req)
        except BaseException:
            await self._redis.delete(idem)
            raise
        await self._redis.set(idem, resp.model_dump_json(), ex=IDEM_TTL_S)
        return resp

    async def _escalate_new(self, device_id: UUID, req: EscalateRequest) -> EscalateResponse:
        now = self._clock.now()
        deadline = now + timedelta(seconds=self.ack_timeout_s)
        links = await self._repo.list_live_for_device(device_id)
        escalation_id = new_uuid7()
        if not links:
            return EscalateResponse(
                escalation_id=escalation_id,
                status="NO_ACTIVE_GUARDIAN",
                channels_attempted=[],
                guardians_targeted=0,
                ack_deadline_at=deadline,
            )
        raised_at = min(_aware(req.occurred_at), now)  # không tin đồng hồ máy chạy trước server
        stale = now - raised_at > self._stale_after
        alerts: dict[UUID, EmergencyAlert] = {
            lk.link_id: build_emergency_alert(
                escalation_id=escalation_id,
                reason=req.reason,
                hsm_state=req.hsm_state,
                axes=list(req.detected_axes),
                label=lk.borrower_display_label,
                raised_at=raised_at,
            )
            for lk in links
        }
        await self._store_pending(device_id, escalation_id, alerts, raised_at)

        if stale:
            # S2-EC45: tin cũ > 45 phút KHÔNG rung chuông SOS / Push / SMS giữa đêm — chỉ gom thành
            # bản tin tóm tắt im lặng (gửi ngay nếu người thân đang mở app, còn lại phát khi nối lại).
            for lk in links:
                digest = AlertDigest(
                    stale_alert_count=1,
                    oldest_raised_at=raised_at,
                    summary_vi="Có 1 cảnh báo cũ đã được ghi nhận. Hãy hỏi thăm người thân của bạn.",
                )
                await self._hub.publish(
                    device_id,
                    RealtimeRole.GUARDIAN,
                    make_frame("EVENT_ALERT_DIGEST", digest, now),
                    link_id=lk.link_id,
                )
                if await self._hub.is_live(device_id, RealtimeRole.GUARDIAN, link_id=lk.link_id):
                    # Đã tóm tắt cho người đang online: gỡ khỏi hàng chờ để không gửi trùng khi nối lại.
                    await self._redis.hdel(
                        pending_alerts_key(device_id), f"{escalation_id}:{lk.link_id}"
                    )
            return EscalateResponse(
                escalation_id=escalation_id,
                status="PENDING_ACK",
                channels_attempted=[],
                guardians_targeted=len(links),
                ack_deadline_at=deadline,
            )

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                ack_timer_key(escalation_id),
                mapping={
                    "status": STATUS_PENDING,
                    "device_id": str(device_id),
                    "raised_at_ms": str(int(now.timestamp() * 1000)),
                    "guardians": str(len(links)),
                    "links": json.dumps([str(lk.link_id) for lk in links]),
                },
            )
            pipe.expire(ack_timer_key(escalation_id), ACK_TIMER_TTL_S)
            # Hạn ACK bền vững trên Redis: instance nào còn sống cũng gửi được SMS (S2-EC44).
            pipe.zadd(ACK_DUE_KEY, {str(escalation_id): int(deadline.timestamp() * 1000)})
            await pipe.execute()
        self.ensure_sweeper()

        outcomes = await asyncio.gather(
            *(self._deliver(device_id, escalation_id, lk, alerts[lk.link_id], now) for lk in links)
        )
        channels: list[EscalationChannel] = [EscalationChannel.WEBSOCKET]
        if any(o.push_attempted for o in outcomes):
            channels.append(EscalationChannel.WEB_PUSH)
        for o in outcomes:
            if o.fallback_channel is not None and o.fallback_channel not in channels:
                channels.append(o.fallback_channel)
        if not all(o.handled for o in outcomes):
            self._spawn_watcher(escalation_id)
        else:
            await self._transition(escalation_id, (STATUS_PENDING,), STATUS_FALLBACK)
            await self._redis.zrem(ACK_DUE_KEY, str(escalation_id))
        return EscalateResponse(
            escalation_id=escalation_id,
            status="PENDING_ACK",
            channels_attempted=channels,
            guardians_targeted=len(links),
            ack_deadline_at=deadline,
        )

    # --- giao tin cho từng người thân --------------------------------------------------------
    async def _deliver(
        self,
        device_id: UUID,
        escalation_id: UUID,
        link: GuardianLink,
        alert: EmergencyAlert,
        now: datetime,
    ) -> _Outcome:
        publish = self._hub.publish(
            device_id,
            RealtimeRole.GUARDIAN,
            make_frame("EVENT_EMERGENCY_ALERT", alert, now),
            link_id=link.link_id,
        )
        push_attempted = link.push_subscription is not None
        push_task = (
            self._push.send(link.push_subscription, build_push_payload(alert))
            if link.push_subscription is not None
            else _none_result()
        )
        _, push_result = await asyncio.gather(publish, push_task)
        if push_result is PushResult.GONE:
            # EC-DB14: subscription chết → cập nhật trạng thái, nhảy thẳng SMS/Email, không chờ 10s.
            await self._repo.mark_push_expired(link.link_id)
            chan, finished = await self._send_fallback(
                device_id, escalation_id, link, alert, "PUSH_EXPIRED_410"
            )
            return _Outcome(link, push_attempted, chan, handled=finished)
        if push_result is not PushResult.DELIVERED:
            live = await self._hub.is_live(device_id, RealtimeRole.GUARDIAN, link_id=link.link_id)
            if not live:
                chan, finished = await self._send_fallback(
                    device_id, escalation_id, link, alert, "NO_LIVE_SOCKET"
                )
                return _Outcome(link, push_attempted, chan, handled=finished)
        return _Outcome(link, push_attempted, None, handled=False)

    async def _send_fallback(
        self,
        device_id: UUID,
        escalation_id: UUID,
        link: GuardianLink,
        alert: EmergencyAlert,
        reason: str,
    ) -> tuple[EscalationChannel | None, bool]:
        """Gửi SMS/Email dự phòng cho 1 link theo 2 pha (claim ``sending`` có hạn → gửi → ``done``
        CHỈ khi thành công). Trả (kênh đã gửi, link đã xong — không cần thử lại nữa). Không bao giờ
        ném lỗi nhà cung cấp ra ngoài (không làm hỏng /escalate)."""
        contact = link.fallback_contact
        if contact is None:
            logger.warning("escalation_no_fallback_contact", extra={"reason": reason})
            return None, True
        key = ack_timer_key(escalation_id)
        now_ms = int(self._clock.now().timestamp() * 1000)
        claim = int(
            await self._claim_send(
                keys=[key],
                args=[str(link.link_id), now_ms, SEND_LEASE_MS, MAX_SEND_TRIES, ACK_TIMER_TTL_S],
            )
        )
        if claim == 0:
            return None, True  # bên khác đã gửi xong
        if claim == -3:
            return None, False  # bên khác đang gửi (inflight còn hạn): không gửi trùng
        if claim == -2:
            logger.error("escalation_fallback_exhausted", extra={"reason": reason})
            return None, True
        if claim == -1:
            logger.error("ack_expired_dropped", extra={"reason": reason})
            return None, True
        ok = False
        try:
            ok = bool(await self._sms.send(contact, build_sms_text(alert)))
        except asyncio.CancelledError:
            await self._finish_send(keys=[key], args=[str(link.link_id), "0"])
            raise
        except Exception:
            logger.exception("escalation_fallback_send_error", extra={"reason": reason})
        await self._finish_send(keys=[key], args=[str(link.link_id), "1" if ok else "0"])
        if not ok:
            logger.error("escalation_fallback_send_failed", extra={"reason": reason})
            return None, False  # sweeper thử lại (có giới hạn MAX_SEND_TRIES)
        channel = EscalationChannel.SMS if contact.channel == "SMS" else EscalationChannel.EMAIL
        payload = EscalationFallback(
            escalation_id=escalation_id,
            channel=channel,
            reason=reason,  # type: ignore[arg-type]
            message_vi="Chưa thấy người thân phản hồi, hệ thống đã gửi tin khẩn qua kênh dự phòng.",
        )
        await self._hub.publish(
            device_id,
            RealtimeRole.BORROWER,
            make_frame("EVENT_ESCALATION_FALLBACK", payload, self._clock.now()),
        )
        return channel, True

    # --- hẹn giờ ACK -----------------------------------------------------------------------
    def _spawn_watcher(self, escalation_id: UUID) -> None:
        """Gia tốc cục bộ: ngủ ``ack_timeout_s`` rồi quét. Mất hẹn giờ này (pod sập) vẫn có
        ``sweep_due`` của các instance khác nhặt lại từ ZSET."""

        async def run() -> None:
            try:
                await self._sleep(self.ack_timeout_s)
                await self.sweep_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("escalation_watcher_failed")
            finally:
                self._watchers.pop(escalation_id, None)

        self._watchers[escalation_id] = asyncio.create_task(
            run(), name=f"ack-timer-{escalation_id}"
        )

    async def sweep_due(self) -> int:
        """Xử lý các leo thang quá hạn ACK: PENDING→FALLBACK nguyên tử rồi gửi SMS/Email.
        An toàn khi nhiều instance cùng gọi (lease trong ZSET + Lua chuyển trạng thái). Mỗi mã xử
        lý độc lập: lỗi ở 1 mã không làm mất mã khác; mã lỗi được thử lại khi lease hết hạn."""
        now_ms = int(self._clock.now().timestamp() * 1000)
        ids = await self._claim_due(keys=[ACK_DUE_KEY], args=[now_ms, SWEEP_BATCH, LEASE_MS])
        handled = 0
        for raw in ids:
            esc = UUID(raw.decode() if isinstance(raw, bytes) else str(raw))
            try:
                # Gia hạn lease RIÊNG cho mã này ngay trước khi xử lý (lô dài không làm hết hạn).
                await self._redis.zadd(
                    ACK_DUE_KEY,
                    {str(esc): int(self._clock.now().timestamp() * 1000) + LEASE_MS},
                    xx=True,
                )
                if await self._sweep_one(esc):
                    handled += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ack_sweep_item_failed")  # giữ lease → tự retry
        return handled

    async def _sweep_one(self, esc: UUID) -> bool:
        key = ack_timer_key(esc)
        moved = await self._transition(esc, (STATUS_PENDING,), STATUS_FALLBACK)
        if moved != 1:
            status = await self._redis.hget(key, "status")
            status_s = status.decode() if isinstance(status, bytes) else status
            if status_s is None:
                # Hash hết hạn trước khi gửi xong: ghi lỗi rõ ràng, không im lặng (cần cảnh báo ops).
                logger.error("ack_expired_dropped", extra={"escalation_id": str(esc)})
                await self._redis.zrem(ACK_DUE_KEY, str(esc))
                return False
            if status_s != STATUS_FALLBACK or await self._redis.hexists(key, "fb_done"):
                await self._redis.zrem(ACK_DUE_KEY, str(esc))  # đã ACK / đã gửi xong
                return False
            # đang FALLBACK nhưng chưa xong: lần trước lỗi giữa chừng → làm tiếp
        if not await self._fallback_for(esc):
            return False  # còn link chưa xong: giữ lease, thử lại khi hết hạn
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, "fb_done", "1")
            pipe.expire(key, ACK_TIMER_TTL_S)  # không tạo key không TTL nếu hash đã hết hạn
            await pipe.execute()
        await self._redis.zrem(ACK_DUE_KEY, str(esc))
        return True

    async def _fallback_for(self, escalation_id: UUID) -> bool:
        """``True`` nếu MỌI link đã xong (gửi được / không có liên hệ / hết số lần thử)."""
        data = await self._redis.hgetall(ack_timer_key(escalation_id))
        flat = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in data.items()
        }
        try:
            device_id = UUID(flat["device_id"])
            link_ids = [UUID(x) for x in json.loads(flat["links"])]
        except (KeyError, ValueError, TypeError):
            return True
        all_finished = True
        for link_id in link_ids:
            if f"done:{link_id}" in flat:
                continue
            link = await self._repo.get_link(link_id)
            raw_alert = await self._redis.hget(
                pending_alerts_key(device_id), f"{escalation_id}:{link_id}"
            )
            if link is None or not raw_alert:
                continue
            alert = EmergencyAlert.model_validate_json(raw_alert)
            _, finished = await self._send_fallback(
                device_id, escalation_id, link, alert, "ACK_TIMEOUT"
            )
            all_finished = all_finished and finished
        return all_finished

    async def _transition(self, escalation_id: UUID, sources: tuple[str, ...], target: str) -> int:
        res = await self._transition_script(
            keys=[ack_timer_key(escalation_id)], args=[*sources, target]
        )
        return int(res)

    # --- CLIENT_ALERT_ACK --------------------------------------------------------------------
    async def acknowledge(self, link: GuardianLink, escalation_id: UUID) -> bool:
        """Người thân xác nhận đã đọc. ``False`` nếu không thuộc thiết bị của link / không tồn tại."""
        raw = await self._redis.get(owner_key(escalation_id))
        text = raw.decode() if isinstance(raw, bytes) else raw
        if not text:
            return False
        try:
            owner = json.loads(text)
            owner_device = UUID(owner["device_id"])
            raised_ms = int(owner["raised_at_ms"])
        except (ValueError, KeyError, TypeError):
            return False
        if owner_device != link.device_id:
            return False  # chống ACK chéo thiết bị
        moved = await self._transition(escalation_id, (STATUS_PENDING, STATUS_FALLBACK), STATUS_ACK)
        await self._redis.zrem(ACK_DUE_KEY, str(escalation_id))
        await self._clear_pending(link.device_id, escalation_id)
        watcher = self._watchers.get(escalation_id)
        if watcher is not None:
            watcher.cancel()
        if moved == 0:  # đã ACK trước đó (người thân khác) — không báo lặp
            return True
        now = self._clock.now()
        latency = max(0, int(now.timestamp() * 1000) - raised_ms)
        payload = EscalationAcknowledged(
            escalation_id=escalation_id,
            guardian_alias=link.guardian_alias,
            ack_latency_ms=latency,
            message_vi=f"{link.guardian_alias} đã nhận được cảnh báo và đang liên hệ với bạn!",
        )
        await self._hub.publish(
            link.device_id,
            RealtimeRole.BORROWER,
            make_frame("EVENT_ESCALATION_ACKNOWLEDGED", payload, now),
        )
        return True

    # --- cảnh báo chờ (S2-EC45) ----------------------------------------------------------------
    async def _store_pending(
        self,
        device_id: UUID,
        escalation_id: UUID,
        alerts: dict[UUID, EmergencyAlert],
        raised_at: datetime,
    ) -> None:
        key = pending_alerts_key(device_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            for link_id, alert in alerts.items():
                pipe.hset(key, f"{escalation_id}:{link_id}", alert.model_dump_json())
            pipe.expire(key, PENDING_TTL_S)
            pipe.set(
                owner_key(escalation_id),
                json.dumps(
                    {
                        "device_id": str(device_id),
                        "raised_at_ms": int(self._clock.now().timestamp() * 1000),
                    }
                ),
                ex=OWNER_TTL_S,
            )
            await pipe.execute()

    async def _clear_pending(self, device_id: UUID, escalation_id: UUID) -> None:
        key = pending_alerts_key(device_id)
        fields = await self._redis.hkeys(key)
        mine = [
            f
            for f in fields
            if (f.decode() if isinstance(f, bytes) else f).startswith(f"{escalation_id}:")
        ]
        if mine:
            await self._redis.hdel(key, *mine)

    async def pending_frames_for(self, link: GuardianLink) -> list[dict[str, Any]]:
        """Frame cần phát khi người thân kết nối: cảnh báo ≤ 45 phút (phát lại, cần ACK) và 1 bản
        tin tóm tắt cho cảnh báo cũ hơn (S2-EC45); bản cũ bị gỡ sau khi tóm tắt."""
        key = pending_alerts_key(link.device_id)
        entries = await self._redis.hgetall(key)
        now = self._clock.now()
        fresh: list[EmergencyAlert] = []
        stale_fields: list[str] = []
        stale_times: list[datetime] = []
        suffix = f":{link.link_id}"
        for raw_field, raw_val in entries.items():
            field = raw_field.decode() if isinstance(raw_field, bytes) else raw_field
            if not field.endswith(suffix):
                continue
            try:
                alert = EmergencyAlert.model_validate_json(raw_val)
            except ValueError:
                stale_fields.append(field)
                continue
            if now - _aware(alert.raised_at) > self._stale_after:
                stale_fields.append(field)
                stale_times.append(_aware(alert.raised_at))
            else:
                fresh.append(alert)
        frames: list[dict[str, Any]] = []
        if stale_times:
            digest = AlertDigest(
                stale_alert_count=len(stale_times),
                oldest_raised_at=min(stale_times),
                summary_vi=(
                    f"Có {len(stale_times)} cảnh báo cũ đã được ghi nhận khi bạn vắng mặt. "
                    "Hãy hỏi thăm người thân của bạn."
                ),
            )
            frames.append(make_frame("EVENT_ALERT_DIGEST", digest, now))
        if stale_fields:
            await self._redis.hdel(key, *stale_fields)
        for alert in sorted(fresh, key=lambda a: a.raised_at):
            frames.append(make_frame("EVENT_EMERGENCY_ALERT", alert, now))
        return frames


class _Outcome:
    __slots__ = ("fallback_channel", "handled", "link", "push_attempted")

    def __init__(
        self,
        link: GuardianLink,
        push_attempted: bool,
        fallback_channel: EscalationChannel | None,
        *,
        handled: bool,
    ) -> None:
        self.link = link
        self.push_attempted = push_attempted
        self.fallback_channel = fallback_channel
        self.handled = handled


async def _none_result() -> PushResult | None:
    return None
