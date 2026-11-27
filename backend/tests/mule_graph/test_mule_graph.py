"""Đồ thị Mule Pre-SIMO: S2-EC27, EC-DB02, EC-DB07, EC-DB09, EC-DB10 (blind index), EC-DB08."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from app.core.clock import FakeClock
from app.core.schemas.ops import EvidenceChannel, SeverityLevel, SimoDossierStatus
from app.mule_graph.repository import InMemoryRepository
from app.mule_graph.service import (
    DEFAULT_CONFIDENCE,
    EMBEDDING_DIM,
    MuleGraphService,
    ReportResult,
    mask_account,
    va_prefix_of,
)
from app.security.blind_index import BlindIndexer, BlindIndexKind
from app.security.envelope import EnvelopeCipher, record_aad
from tests.security._helpers import (
    PEPPER_MULE_V1,
    PEPPER_MULE_V2,
    make_settings,
)

BIN = "970436"
VQR = EvidenceChannel.VIETQR_CRYPTO_DECODED
OCR = EvidenceChannel.OCR_RECEIPT_EXTRACTED


async def _report(
    svc: MuleGraphService,
    clock: FakeClock,
    account: str,
    device: UUID | None = None,
    channel: EvidenceChannel = VQR,
    scam_script_vec: tuple[float, ...] | None = None,
) -> ReportResult:
    return await svc.report_mule_account(
        bank_bin=BIN,
        account_no=account,
        reporter_device_id=device or uuid4(),
        channel=channel,
        now=clock.now(),
        scam_script_vec=scam_script_vec,
    )


async def test_s2_ec27_single_device_cannot_confirm_even_when_repeating(
    mule_service: MuleGraphService, fake_clock: FakeClock
) -> None:
    """S2-EC27: 1 thiết bị gửi 100 lần vẫn chỉ 1 phiếu, không lên quorum, độ tin cậy bị chặn."""
    dev = uuid4()
    res = None
    for _ in range(100):
        res = await _report(mule_service, fake_clock, "1903123456789", dev)
    assert res is not None
    assert res.distinct_device_quorum == 1
    assert res.status is SimoDossierStatus.OBSERVING
    assert res.threat_confidence == DEFAULT_CONFIDENCE
    assert res.newly_counted is False


async def test_s2_ec27_two_independent_devices_reach_quorum(
    mule_service: MuleGraphService, fake_clock: FakeClock
) -> None:
    first = await _report(mule_service, fake_clock, "1903123456789")
    assert first.status is SimoDossierStatus.OBSERVING
    second = await _report(mule_service, fake_clock, "1903123456789", channel=OCR)
    assert second.node_id == first.node_id
    assert second.distinct_device_quorum == 2
    assert second.status is SimoDossierStatus.QUORUM_REACHED
    assert second.threat_confidence > DEFAULT_CONFIDENCE


async def test_ec_db07_ten_simultaneous_reporters_do_not_lose_quorum(
    mule_service: MuleGraphService, fake_clock: FakeClock, repo: InMemoryRepository
) -> None:
    """EC-DB07: 10 nạn nhân quét cùng 1 QR cùng lúc → quorum đúng 10 (đếm lại từ bảng cạnh)."""
    results = await asyncio.gather(
        *(_report(mule_service, fake_clock, "1903123456789") for _ in range(10))
    )
    node_id = results[0].node_id
    assert {r.node_id for r in results} == {node_id}
    assert repo.nodes[node_id].distinct_device_quorum == 10
    assert len(repo.nodes) == 1


async def test_envelope_and_blind_index_no_plaintext_account(
    mule_service: MuleGraphService,
    fake_clock: FakeClock,
    repo: InMemoryRepository,
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
) -> None:
    res = await _report(mule_service, fake_clock, "1903123456789")
    node = repo.nodes[res.node_id]
    assert node.account_hmac == indexer.index(BlindIndexKind.MULE_ACCOUNT, f"{BIN}:1903123456789")
    assert b"1903123456789" not in node.account_cipher_envelope
    assert "1903123456789" not in repr(node) or node.masked_account_display == "***789"
    plain = cipher.decrypt(
        node.account_cipher_envelope,
        aad=record_aad("mule_account_nodes", "account_cipher_envelope", node.id),
    )
    assert plain == f"{BIN}:1903123456789".encode()
    assert node.masked_account_display == mask_account("1903123456789") == "***789"


async def test_ec_db09_alias_account_and_leading_zero_preserved(
    mule_service: MuleGraphService,
    fake_clock: FakeClock,
    repo: InMemoryRepository,
    cipher: EnvelopeCipher,
) -> None:
    """EC-DB09: STK chữ (alias) và STK có số 0 đầu được chuẩn hóa A-Z0-9, không ép BIGINT."""
    alias = await _report(mule_service, fake_clock, "hotro-huyvay 247")
    zero = await _report(mule_service, fake_clock, "007100.123 456")
    again = await _report(mule_service, fake_clock, "HOTROHUYVAY247")
    assert again.node_id == alias.node_id  # chuẩn hóa chữ hoa/bỏ dấu câu → cùng 1 đỉnh
    secret = cipher.decrypt(
        repo.nodes[zero.node_id].account_cipher_envelope,
        aad=record_aad("mule_account_nodes", "account_cipher_envelope", zero.node_id),
    )
    assert secret == f"{BIN}:007100123456".encode()
    assert repo.nodes[alias.node_id].va_prefix_hmac is None
    assert va_prefix_of("HOTROHUYVAY247") is None
    assert va_prefix_of("007100123456") == "00710012"


async def test_ec_db02_dynamic_va_range_clusters_at_cluster_level(
    mule_service: MuleGraphService, fake_clock: FakeClock, repo: InMemoryRepository
) -> None:
    """EC-DB02: mỗi nạn nhân 1 VA khác đuôi, cùng dải Merchant → gom 1 cụm, quorum ở MỨC CỤM."""
    a = await _report(mule_service, fake_clock, "9638880001")
    assert a.cluster_id is None and a.status is SimoDossierStatus.OBSERVING
    b = await _report(mule_service, fake_clock, "9638880002")
    assert b.cluster_id is not None and b.is_dynamic_va_range
    assert b.cluster_distinct_devices == 2
    assert b.cluster_severity is SeverityLevel.CRITICAL_WAVE
    node_a = repo.nodes[a.node_id]
    assert node_a.cluster_id == b.cluster_id and node_a.is_dynamic_va_range
    assert node_a.va_prefix_hmac is not None
    assert node_a.account_hmac != repo.nodes[b.node_id].account_hmac
    assert node_a.distinct_device_quorum == 1
    # từng STK vẫn cần quorum riêng
    assert node_a.simo_dossier_status is SimoDossierStatus.OBSERVING
    assert b.status is SimoDossierStatus.OBSERVING


async def test_ec_db02_range_signal_does_not_confirm_unreported_account(
    mule_service: MuleGraphService, fake_clock: FakeClock, repo: InMemoryRepository
) -> None:
    """Review M2: A báo 1012345678, B báo 1012349999 (cùng 6 số đầu) → STK nào cũng chỉ 1 phiếu,
    không QUORUM_REACHED, độ tin cậy giữ 0.35 — chỉ cụm bị đánh dấu."""
    a = await _report(mule_service, fake_clock, "1012345678")
    b = await _report(mule_service, fake_clock, "1012349999")
    for node_id in (a.node_id, b.node_id):
        node = repo.nodes[node_id]
        assert node.simo_dossier_status is SimoDossierStatus.OBSERVING
        assert node.threat_confidence == DEFAULT_CONFIDENCE
        assert node.distinct_device_quorum == 1
    assert b.cluster_id is not None


async def test_single_device_flooding_range_cannot_inflate_other_account(
    mule_service: MuleGraphService, fake_clock: FakeClock, repo: InMemoryRepository
) -> None:
    """Review M3: 1 thiết bị bơm 8 STK cùng dải; STK thứ 9 chỉ 1 thiết bị khác báo → vẫn 0.35."""
    attacker = uuid4()
    for i in range(8):
        await _report(mule_service, fake_clock, f"96388800{i:02d}", attacker, channel=VQR)
    victim = await _report(mule_service, fake_clock, "9638880099", channel=VQR)
    assert victim.status is SimoDossierStatus.OBSERVING
    assert victim.threat_confidence == DEFAULT_CONFIDENCE


def test_confidence_counts_each_device_once_with_strongest_channel() -> None:
    from datetime import UTC, datetime

    from app.mule_graph.models import MuleEdgeRecord
    from app.mule_graph.service import compute_confidence

    now = datetime(2026, 1, 1, tzinfo=UTC)
    node = uuid4()
    d1, d2 = uuid4(), uuid4()

    def edge(dev: UUID, ch: EvidenceChannel) -> MuleEdgeRecord:
        return MuleEdgeRecord(node, dev, ch, now)

    many = [edge(d1, ch) for ch in EvidenceChannel] + [
        edge(d2, EvidenceChannel.VOICE_CHAT_REPORTED)
    ]
    # d1 → 0.5 (VietQR), d2 → 0.2: 1 - 0.5*0.8 = 0.6
    assert compute_confidence(many, 2) == 0.6


async def test_ec_db02_unrelated_account_and_other_bin_not_clustered(
    mule_service: MuleGraphService, fake_clock: FakeClock
) -> None:
    await _report(mule_service, fake_clock, "9638880001")
    other_prefix = await _report(mule_service, fake_clock, "5551110001")
    assert other_prefix.cluster_id is None
    other_bin = await mule_service.report_mule_account(
        bank_bin="970422",
        account_no="9638880002",
        reporter_device_id=uuid4(),
        channel=VQR,
        now=fake_clock.now(),
    )
    assert other_bin.cluster_id is None


async def test_ec_db02_same_device_two_va_does_not_fake_quorum(
    mule_service: MuleGraphService, fake_clock: FakeClock
) -> None:
    """Sybil: 1 thiết bị báo 2 VA cùng dải → cụm có nhưng chưa đủ 2 thiết bị độc lập."""
    dev = uuid4()
    await _report(mule_service, fake_clock, "9638880001", dev)
    res = await _report(mule_service, fake_clock, "9638880002", dev)
    assert res.cluster_id is not None
    assert res.status is SimoDossierStatus.OBSERVING
    assert res.threat_confidence == DEFAULT_CONFIDENCE
    assert res.cluster_distinct_devices == 1


async def test_cluster_becomes_critical_wave_only_within_6h_window(
    mule_service: MuleGraphService, fake_clock: FakeClock, repo: InMemoryRepository
) -> None:
    """File 3 §5.2: CRITICAL_WAVE cần >= 2 thiết bị độc lập trong cửa sổ trượt 6 giờ."""
    await _report(mule_service, fake_clock, "9638880001")
    fake_clock.advance(7 * 3600)
    late = await _report(mule_service, fake_clock, "9638880002")
    assert late.cluster_id is not None
    assert repo.clusters[late.cluster_id].severity_level is SeverityLevel.WATCH
    fake_clock.advance(60)
    await _report(mule_service, fake_clock, "9638880003")
    assert repo.clusters[late.cluster_id].severity_level is SeverityLevel.CRITICAL_WAVE


async def test_cluster_centroid_is_robust_to_outlier_vector(
    mule_service: MuleGraphService, fake_clock: FakeClock, repo: InMemoryRepository
) -> None:
    """EC-AI20: 1 vector rác ngoại lai không kéo lệch tâm cụm."""
    good = tuple([1.0] + [0.0] * (EMBEDDING_DIM - 1))
    near = tuple([0.98, 0.1] + [0.0] * (EMBEDDING_DIM - 2))
    junk = tuple([0.0, 0.0, 1.0] + [0.0] * (EMBEDDING_DIM - 3))
    await _report(mule_service, fake_clock, "9638880001", scam_script_vec=good)
    await _report(mule_service, fake_clock, "9638880002", scam_script_vec=near)
    res = await _report(mule_service, fake_clock, "9638880003", scam_script_vec=junk)
    assert res.cluster_id is not None
    centroid = repo.clusters[res.cluster_id].centroid_embedding
    assert centroid is not None
    assert centroid[2] == 0.0 and centroid[0] > 0.9


async def test_wrong_embedding_dimension_rejected(
    mule_service: MuleGraphService, fake_clock: FakeClock
) -> None:
    with pytest.raises(ValueError):
        await _report(mule_service, fake_clock, "1903123456789", scam_script_vec=(1.0, 2.0))


async def test_ec_db08_pepper_rotation_finds_old_node_and_rehashes(
    repo: InMemoryRepository, cipher: EnvelopeCipher, fake_clock: FakeClock
) -> None:
    old = BlindIndexer.from_settings(make_settings(pepper_mule_account=_secret(PEPPER_MULE_V1)))
    svc_old = MuleGraphService(repo, old, cipher)
    first = await _report(svc_old, fake_clock, "1903123456789")
    rotated = BlindIndexer.from_settings(
        make_settings(
            pepper_mule_account=_secret(PEPPER_MULE_V2),
            pepper_mule_account_prev=_secret(PEPPER_MULE_V1),
            pepper_mule_account_version=2,
        )
    )
    svc_new = MuleGraphService(repo, rotated, cipher)
    second = await _report(svc_new, fake_clock, "1903123456789")
    assert second.node_id == first.node_id and len(repo.nodes) == 1
    assert repo.nodes[first.node_id].account_hmac == rotated.index(
        BlindIndexKind.MULE_ACCOUNT, f"{BIN}:1903123456789"
    )
    assert repo.nodes[first.node_id].pepper_version == 2
    assert second.distinct_device_quorum == 2


def _secret(value: str):  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    return SecretStr(value)


async def test_ec_db08_va_prefix_rehashed_and_range_not_split_after_rotation(
    repo: InMemoryRepository, cipher: EnvelopeCipher, fake_clock: FakeClock
) -> None:
    old = BlindIndexer.from_settings(make_settings(pepper_mule_account=_secret(PEPPER_MULE_V1)))
    first = await _report(MuleGraphService(repo, old, cipher), fake_clock, "9638880001")
    rotated = BlindIndexer.from_settings(
        make_settings(
            pepper_mule_account=_secret(PEPPER_MULE_V2),
            pepper_mule_account_prev=_secret(PEPPER_MULE_V1),
            pepper_mule_account_version=2,
        )
    )
    svc = MuleGraphService(repo, rotated, cipher)
    second = await _report(svc, fake_clock, "9638880002")
    assert second.cluster_id is not None, "dải VA không bị tách đôi sau xoay pepper"
    new_va = rotated.index(BlindIndexKind.MULE_ACCOUNT, f"{BIN}:VAPFX963888")
    assert repo.nodes[first.node_id].va_prefix_hmac == new_va
    assert repo.nodes[second.node_id].va_prefix_hmac == new_va
    assert repo.nodes[first.node_id].cluster_id == second.cluster_id


async def test_non_finite_or_zero_embedding_rejected(
    mule_service: MuleGraphService, fake_clock: FakeClock
) -> None:
    bad = tuple([float("nan")] + [0.0] * (EMBEDDING_DIM - 1))
    zero = tuple([0.0] * EMBEDDING_DIM)
    for vec in (bad, zero):
        with pytest.raises(ValueError):
            await _report(mule_service, fake_clock, "1903123456789", scam_script_vec=vec)
