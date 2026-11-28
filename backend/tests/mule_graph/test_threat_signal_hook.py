"""EC-DB07/S2-EC27: quorum/leo thang cụm của đồ thị Mule phát tín hiệu lên live feed Ops (T21)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import fakeredis

from app.copilot.feed import THREAT_CHANNEL, publish_threat_signal, threat_signal_frames
from app.core.clock import FakeClock
from app.core.schemas.ops import EvidenceChannel
from app.core.schemas.sse import OpsThreatSignal
from app.mule_graph.repository import InMemoryRepository
from app.mule_graph.service import MuleGraphService
from app.security.blind_index import BlindIndexer
from app.security.envelope import EnvelopeCipher
from app.services.audit_pipeline.container import _make_threat_sink

BIN = "970436"
VQR = EvidenceChannel.VIETQR_CRYPTO_DECODED
ACCOUNT = "9638880001"


async def _report(svc: MuleGraphService, clock: FakeClock, account: str = ACCOUNT) -> None:
    await svc.report_mule_account(
        bank_bin=BIN,
        account_no=account,
        reporter_device_id=uuid4(),
        channel=VQR,
        now=clock.now(),
    )


def _service(
    repo: InMemoryRepository, indexer: BlindIndexer, cipher: EnvelopeCipher, sink: object
) -> MuleGraphService:
    return MuleGraphService(repo, indexer, cipher, on_threat_signal=sink)  # type: ignore[arg-type]


async def test_ec_db07_quorum_publishes_masked_signal_once(
    repo: InMemoryRepository,
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
    fake_clock: FakeClock,
) -> None:
    got: list[OpsThreatSignal] = []

    async def sink(signal: OpsThreatSignal) -> None:
        got.append(signal)

    svc = _service(repo, indexer, cipher, sink)
    await _report(svc, fake_clock)
    assert got == []  # 1 thiết bị: chưa quorum
    await _report(svc, fake_clock)
    assert [s.kind for s in got] == ["QUORUM_REACHED"]
    await _report(svc, fake_clock)  # thiết bị thứ 3: không phát lại
    assert len(got) == 1
    dumped = got[0].model_dump_json()
    assert ACCOUNT not in dumped  # không lộ STK thô
    assert got[0].masked_account_display == "***001"


async def test_ec_db02_cluster_critical_wave_publishes_escalation(
    repo: InMemoryRepository,
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
    fake_clock: FakeClock,
) -> None:
    got: list[OpsThreatSignal] = []

    async def sink(signal: OpsThreatSignal) -> None:
        got.append(signal)

    svc = _service(repo, indexer, cipher, sink)
    # Hai STK khác đuôi cùng dải VA (6 số đầu giống nhau), 2 thiết bị khác nhau -> CRITICAL_WAVE.
    await _report(svc, fake_clock, "9638880001")
    await _report(svc, fake_clock, "9638880002")
    kinds = [s.kind for s in got]
    assert "CLUSTER_ESCALATED" in kinds
    esc = next(s for s in got if s.kind == "CLUSTER_ESCALATED")
    assert esc.severity_level == "CRITICAL_WAVE"
    assert esc.cluster_id is not None
    assert kinds.count("CLUSTER_ESCALATED") == 1
    await _report(svc, fake_clock, "9638880003")
    assert [s.kind for s in got].count("CLUSTER_ESCALATED") == 1  # đã CRITICAL: không lặp


async def test_ec_db07_publish_failure_does_not_break_report(
    repo: InMemoryRepository,
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
    fake_clock: FakeClock,
) -> None:
    async def boom(signal: OpsThreatSignal) -> None:
        raise ConnectionError("redis down")

    svc = _service(repo, indexer, cipher, boom)
    await _report(svc, fake_clock)
    result = await svc.report_mule_account(
        bank_bin=BIN,
        account_no=ACCOUNT,
        reporter_device_id=uuid4(),
        channel=VQR,
        now=fake_clock.now(),
    )
    assert result.distinct_device_quorum == 2  # báo cáo vẫn được ghi


async def test_ec_db07_redis_sink_reaches_live_feed(
    repo: InMemoryRepository,
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    svc = _service(repo, indexer, cipher, _make_threat_sink(redis))

    async def consume() -> list[str]:
        return [f async for f in threat_signal_frames(redis, max_seconds=5, max_events=1)]

    task = asyncio.create_task(consume())
    for _ in range(50):  # chờ subscriber đăng ký kênh
        await asyncio.sleep(0.02)
        subs = await redis.pubsub_numsub(THREAT_CHANNEL)
        if subs and subs[0][1] > 0:
            break
    await _report(svc, fake_clock)
    await _report(svc, fake_clock)
    frames = await asyncio.wait_for(task, timeout=6)
    assert len(frames) == 1
    assert "ops.threat_signal" in frames[0]
    assert "QUORUM_REACHED" in frames[0]
    assert ACCOUNT not in frames[0]
    assert await publish_threat_signal(redis, _dummy_signal()) == 0  # hết subscriber


def _dummy_signal() -> OpsThreatSignal:
    from datetime import UTC, datetime

    return OpsThreatSignal(
        signal_id=uuid4(),
        kind="MULE_REPORTED",
        severity_level="WATCH",
        occurred_at=datetime.now(UTC),
        summary_vi="x",
    )
