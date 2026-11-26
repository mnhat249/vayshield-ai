"""ShieldStateStore: Redis HASH, CAS chống lost-update, race B2B + edge, fallback EC-DB11."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import fakeredis
import pytest
from redis.asyncio import Redis

from app.core.errors import AppError
from app.core.schemas.common import HsmState
from app.shield_state.fibers import EvidenceSource, FiberSupervisor, LoanJourneyFiber
from app.shield_state.hsm import HsmEvent
from app.shield_state.keys import shield_state_key
from app.shield_state.risk import HOUR_MS, MINUTE_MS, AudioMode, RiskTier, SensorContext
from app.shield_state.store import DeviceShieldSnapshot, ShieldStateStore, SnapshotSource

DEV = "019361a0-0000-7000-8000-000000000001"
NOW = 1_795_543_200_000
E = HsmEvent
S = HsmState


class BarrierStore(ShieldStateStore):
    """Ép N tác vụ cùng đọc phiên bản cũ trước khi bất kỳ ai ghi → buộc xảy ra xung đột CAS."""

    def __init__(self, redis: Redis, parties: int) -> None:
        super().__init__(redis)
        self._barrier = asyncio.Barrier(parties)
        self._first_round = True
        self.reads = 0

    async def _read_raw(self, device_id: str) -> dict[str, str]:
        raw = await super()._read_raw(device_id)
        self.reads += 1
        if self._first_round:
            await self._barrier.wait()
            self._first_round = False
        return raw


async def test_hash_roundtrip_and_fields(redis: Redis) -> None:
    store = ShieldStateStore(redis)
    snap, fiber = await store.arm_shield(
        DEV,
        "default",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=NOW,
        event_id="n1",
        source=EvidenceSource.EDGE_NOTIF,
    )
    assert fiber is not None
    assert snap.version == 1 and fiber.state is S.S4_SHIELD_ARMED_24H
    h = await redis.hgetall(shield_state_key(DEV))
    assert h["state"] == "S4_SHIELD_ARMED_24H"
    assert h["disb_ts"] == str(NOW)
    assert h["shield_expires_at"] == str(NOW + 24 * HOUR_MS)
    assert float(h["base_log_odds"]) == pytest.approx(-0.7)
    ttl = await redis.pttl(shield_state_key(DEV))
    assert 23 * HOUR_MS < ttl <= 24 * HOUR_MS
    loaded = await store.load(DEV, NOW)
    assert loaded.source is SnapshotSource.REDIS and loaded.fibers == (fiber,)


async def test_ec_db07_concurrent_writers_no_lost_update(redis: Redis) -> None:
    """EC-DB07 (lost update): 2 tác vụ đọc cùng version rồi cùng ghi → CAS loại 1 bên, bên đó đọc
    lại và áp lại → cả 2 cập nhật đều còn."""
    store = BarrierStore(redis, parties=2)
    await asyncio.gather(
        store.apply_event(DEV, "appA", E.E1_APK_INSTALLED, now_ms=NOW, event_id="a"),
        store.apply_event(DEV, "appB", E.E1_APK_INSTALLED, now_ms=NOW, event_id="b"),
    )
    snap = await store.load(DEV, NOW)
    assert {f.fiber_key for f in snap.fibers} == {"appA", "appB"}
    assert snap.version == 2
    assert store.reads >= 3  # có ít nhất 1 lần đọc lại sau xung đột


async def test_ec_db07_many_concurrent_writers(redis: Redis) -> None:
    store = ShieldStateStore(redis)
    await asyncio.gather(
        *(
            store.apply_event(DEV, f"app{i}", E.E1_APK_INSTALLED, now_ms=NOW + i, event_id=f"e{i}")
            for i in range(8)
        )
    )
    snap = await store.load(DEV, NOW + 10)
    assert len(snap.fibers) == 8


async def test_ec43_b2b_webhook_and_edge_notif_same_second_race(redis: Redis) -> None:
    """S2-EC43: webhook B2B + thông báo trên máy cùng giây, ghi đồng thời → 1 phiên duy nhất,
    MAX(shield_expires_at), nguồn bằng chứng B2B_CONFIRMED + EDGE_NOTIF."""
    store = BarrierStore(redis, parties=2)
    await asyncio.gather(
        store.arm_shield(
            DEV,
            "default",
            E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
            now_ms=NOW,
            event_id="notif",
            source=EvidenceSource.EDGE_NOTIF,
        ),
        store.arm_shield(
            DEV,
            "fintechX:loan9",
            E.E5_B2B_WEBHOOK,
            now_ms=NOW + 300,
            event_id="b2b",
            source=EvidenceSource.B2B_CONFIRMED,
            shield_ms=36 * HOUR_MS,
        ),
    )
    snap = await store.load(DEV, NOW + 1000)
    assert len(snap.fibers) == 1
    f = snap.fibers[0]
    assert f.fiber_key == "fintechX:loan9"
    assert f.evidence_sources == {EvidenceSource.B2B_CONFIRMED, EvidenceSource.EDGE_NOTIF}
    assert f.shield_expires_at_ms == NOW + 300 + 36 * HOUR_MS
    assert f.state is S.S4_SHIELD_ARMED_24H
    # Cùng một khoản tiền → đúng 1 bằng chứng DISBURSEMENT (không cộng w_disb 2 lần).
    assert len(f.evidence) == 1 and f.evidence[0].event_id in {"notif", "b2b"}
    assert FiberSupervisor(snap.fibers).base_log_odds(NOW + 1000) == pytest.approx(-3.5 + 2.8)


async def test_evaluate_bank_open_disbursement_in_call_95_7(redis: Redis) -> None:
    store = ShieldStateStore(redis)
    await store.arm_shield(
        DEV,
        "default",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=NOW,
        event_id="n",
        source=EvidenceSource.EDGE_NOTIF,
    )
    ev = await store.evaluate_bank_open(
        DEV,
        "sess1",
        "open-1",
        now_ms=NOW + 5 * MINUTE_MS,
        sensors=SensorContext(audio_mode=AudioMode.IN_CALL),
    )
    assert round(ev.probability * 100, 1) == 95.7
    assert ev.tier is RiskTier.RED_HARD_INTERCEPTION and ev.open_count_15m == 1


async def test_ec08_ec31_bank_reopens_escalate_via_store(redis: Redis) -> None:
    """S2-EC31: lần mở đầu (MODE_NORMAL, 120 phút vàng) → Cam; S2-EC08: mở lại liên tục → Đỏ."""
    store = ShieldStateStore(redis)
    await store.arm_shield(
        DEV,
        "default",
        E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        now_ms=NOW,
        event_id="n",
        source=EvidenceSource.EDGE_NOTIF,
    )
    tiers = []
    for i in range(3):
        ev = await store.evaluate_bank_open(
            DEV, "sess1", f"open-{i}", now_ms=NOW + (10 + i) * MINUTE_MS, sensors=SensorContext()
        )
        tiers.append(ev.tier)
    assert tiers == [
        RiskTier.ORANGE_SOFT_CHECK,  # σ(−0.7) = 33% nhưng sàn 120 phút vàng
        RiskTier.ORANGE_SOFT_CHECK,  # σ(0.2) = 55%
        RiskTier.RED_HARD_INTERCEPTION,  # σ(1.1) = 75.03%
    ]


# --- EC-DB11: Redis mất / chậm --------------------------------------------------------------


def _down_client() -> fakeredis.FakeAsyncRedis:
    server = fakeredis.FakeServer()
    server.connected = False
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)


def _pg_fiber() -> LoanJourneyFiber:
    return LoanJourneyFiber(
        fiber_key="default",
        session_id="019361a0-0000-7000-8000-0000000000aa",
        state=S.S6_COOLING_OFF_LOCK,
        entered_at_ms=NOW,
        updated_at_ms=NOW,
        shield_expires_at_ms=NOW + 24 * HOUR_MS,
    )


async def _pg_reader(device_id: str) -> Sequence[LoanJourneyFiber] | None:
    return [_pg_fiber()] if device_id == DEV else None


async def test_ec_db11_redis_down_reads_through_pg_fallback() -> None:
    client = _down_client()
    store = ShieldStateStore(client, fallback=_pg_reader)
    snap = await store.load(DEV, NOW)
    assert snap.source is SnapshotSource.PG_FALLBACK and snap.degraded
    assert snap.state is S.S6_COOLING_OFF_LOCK
    await client.aclose()


async def test_ec_db11_redis_slow_over_80ms_falls_back(
    redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow_hgetall(*_a: object, **_k: object) -> dict[str, str]:
        await asyncio.sleep(0.5)
        return {}

    monkeypatch.setattr(redis, "hgetall", slow_hgetall)
    store = ShieldStateStore(redis, fallback=_pg_reader, redis_timeout_ms=80)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    snap = await store.load(DEV, NOW)
    assert loop.time() - t0 < 0.4
    assert snap.source is SnapshotSource.PG_FALLBACK and snap.degraded


async def test_ec_db11_no_fallback_returns_safe_armed_state() -> None:
    client = _down_client()
    snap = await ShieldStateStore(client).load(DEV, NOW)
    assert snap.source is SnapshotSource.SAFE_DEFAULT and snap.degraded
    assert snap.state is S.S4_SHIELD_ARMED_24H
    await client.aclose()


async def test_ec_db11_fallback_failure_returns_safe_state() -> None:
    async def broken(_d: str) -> Sequence[LoanJourneyFiber] | None:
        raise RuntimeError("pg down too")

    client = _down_client()
    snap = await ShieldStateStore(client, fallback=broken).load(DEV, NOW)
    assert snap.source is SnapshotSource.SAFE_DEFAULT
    await client.aclose()


async def test_ec_db11_cache_miss_after_restart_rehydrates_from_pg(redis: Redis) -> None:
    store = ShieldStateStore(redis, fallback=_pg_reader)
    snap = await store.load(DEV, NOW)
    assert snap.source is SnapshotSource.PG_FALLBACK and not snap.degraded
    again = await store.load(DEV, NOW)
    assert again.source is SnapshotSource.REDIS  # đã tái nạp cache
    assert again.state is S.S6_COOLING_OFF_LOCK


async def test_ec_db11_corrupt_cache_treated_as_miss(redis: Redis) -> None:
    await redis.hset(shield_state_key(DEV), mapping={"fibers": "{not json", "version": "3"})
    snap = await ShieldStateStore(redis, fallback=_pg_reader).load(DEV, NOW)
    assert snap.state is S.S6_COOLING_OFF_LOCK


async def test_ec_db11_write_when_redis_down_is_503() -> None:
    client = _down_client()
    with pytest.raises(AppError) as ei:
        await ShieldStateStore(client).apply_event(
            DEV, "k", E.E1_APK_INSTALLED, now_ms=NOW, event_id="x"
        )
    assert ei.value.status_code == 503
    await client.aclose()


async def test_ec_db11_bank_open_eval_degraded_is_at_least_orange() -> None:
    client = _down_client()
    ev = await ShieldStateStore(client).evaluate_bank_open(
        DEV, "s", "o", now_ms=NOW, sensors=SensorContext()
    )
    assert ev.degraded and ev.tier is RiskTier.ORANGE_SOFT_CHECK
    await client.aclose()


async def test_cas_retry_exhaustion_is_409(redis: Redis) -> None:
    class AlwaysConflict(ShieldStateStore):
        async def _cas_write(self, *_a: object, **_k: object) -> int:
            return -1

    with pytest.raises(AppError) as ei:
        await AlwaysConflict(redis, max_cas_retries=3).apply_event(
            DEV, "k", E.E1_APK_INSTALLED, now_ms=NOW, event_id="x"
        )
    assert ei.value.status_code == 409


# --- EC-DB11: Redis sống nhưng mất khóa + PG lỗi → không fail-open ---------------------------


def _s4_pg_fiber() -> LoanJourneyFiber:
    return LoanJourneyFiber(
        fiber_key="loan-A",
        session_id="sess-pg",
        state=S.S4_SHIELD_ARMED_24H,
        entered_at_ms=NOW,
        updated_at_ms=NOW,
        shield_expires_at_ms=NOW + 24 * HOUR_MS,
    )


async def test_ec_db11_cache_miss_and_pg_error_is_safe_default_not_empty(redis: Redis) -> None:
    """EC-DB11: Redis sống, cache trống, PG ném ConnectionError → SAFE_DEFAULT degraded (không
    phải EMPTY S0) và mở app ngân hàng tối thiểu Mức Cam (không PASS_SILENT)."""

    async def pg_down(_d: str) -> Sequence[LoanJourneyFiber] | None:
        raise ConnectionError("pg down")

    store = ShieldStateStore(redis, fallback=pg_down)
    snap = await store.load(DEV, NOW)
    assert snap.source is SnapshotSource.SAFE_DEFAULT and snap.degraded
    assert snap.state is S.S4_SHIELD_ARMED_24H
    ev = await store.evaluate_bank_open(DEV, "sess1", "ev1", now_ms=NOW, sensors=SensorContext())
    assert ev.degraded and ev.tier is not RiskTier.PASS_SILENT
    assert ev.tier is RiskTier.ORANGE_SOFT_CHECK
    assert await redis.hget(shield_state_key(DEV), "fibers") is None  # không ghi cache rỗng


async def test_ec_db11_pg_empty_when_redis_alive_is_empty_not_degraded(redis: Redis) -> None:
    """PG trả về "không có dữ liệu" (None) khác với PG lỗi: EMPTY, không degraded."""

    async def pg_none(_d: str) -> Sequence[LoanJourneyFiber] | None:
        return None

    snap = await ShieldStateStore(redis, fallback=pg_none).load(DEV, NOW)
    assert snap.source is SnapshotSource.EMPTY and not snap.degraded


async def test_ec_db11_mutate_with_pg_blip_does_not_poison_cache(redis: Redis) -> None:
    """EC-DB11: PG giữ luồng S4; Redis restart (cache trống); PG chập chờn đúng lúc mutate(E6) →
    503, KHÔNG ghi ``fibers=[]``; lần load sau vẫn đọc lại PG và còn Khiên."""
    calls = {"n": 0}

    async def flaky(_d: str) -> Sequence[LoanJourneyFiber] | None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("pg blip")
        return [_s4_pg_fiber()]

    store = ShieldStateStore(redis, fallback=flaky)
    with pytest.raises(AppError) as ei:
        await store.mutate(
            DEV, NOW + 1000, lambda s: s.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=NOW + 1000)
        )
    assert ei.value.status_code == 503
    assert await redis.hget(shield_state_key(DEV), "fibers") is None
    assert await redis.exists(shield_state_key(DEV)) == 0
    snap = await store.load(DEV, NOW + 2000)
    assert snap.source is SnapshotSource.PG_FALLBACK
    assert snap.state is S.S4_SHIELD_ARMED_24H
    assert snap.shield_expires_at_ms == NOW + 24 * HOUR_MS
    # Sau khi PG hồi phục, E6 áp đúng lên luồng thật.
    after, _ = await store.mutate(
        DEV, NOW + 3000, lambda s: s.broadcast(E.E6_BANKING_OR_WALLET_OPENED, now_ms=NOW + 3000)
    )
    assert after.state is S.S5_CRITICAL_INTERCEPTION


async def test_ec_db11_redis_down_and_pg_error_is_safe_default() -> None:
    async def pg_down(_d: str) -> Sequence[LoanJourneyFiber] | None:
        raise ConnectionError("pg down")

    client = _down_client()
    snap = await ShieldStateStore(client, fallback=pg_down).load(DEV, NOW)
    assert snap.source is SnapshotSource.SAFE_DEFAULT and snap.degraded
    await client.aclose()


async def test_bank_open_uses_fresh_base_when_cas_writes_in_between(redis: Redis) -> None:
    """TOCTOU: snapshot đọc lúc chưa có giải ngân (nền −3.5); trước khi Lua chạy, CAS ghi khoản
    giải ngân (nền −0.7, version tăng) → Lua phải dùng nền mới, không dùng nền cũ."""
    writer = ShieldStateStore(redis)

    class RacingStore(ShieldStateStore):
        async def load(self, device_id: str, now_ms: int) -> DeviceShieldSnapshot:
            snap = await super().load(device_id, now_ms)
            await writer.arm_shield(
                device_id,
                "default",
                E.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
                now_ms=now_ms,
                event_id="n",
                source=EvidenceSource.EDGE_NOTIF,
            )
            return snap

    ev = await RacingStore(redis).evaluate_bank_open(
        DEV, "sess1", "o1", now_ms=NOW, sensors=SensorContext()
    )
    assert ev.log_odds == pytest.approx(-0.7)


@pytest.mark.redis_real
async def test_ec_db07_cas_on_real_redis(real_redis: Redis, uid: str) -> None:
    dev = f"dev-{uid}"
    store = ShieldStateStore(real_redis)
    try:
        await asyncio.gather(
            *(
                store.apply_event(dev, f"app{i}", E.E1_APK_INSTALLED, now_ms=NOW, event_id=f"e{i}")
                for i in range(6)
            )
        )
        snap = await store.load(dev, NOW)
        assert len(snap.fibers) == 6
        ev = await store.evaluate_bank_open(
            dev, f"s-{uid}", "o1", now_ms=NOW, sensors=SensorContext()
        )
        assert ev.open_count_15m == 1
    finally:
        await real_redis.delete(shield_state_key(dev), f"vs:smurf:win:s-{uid}")
