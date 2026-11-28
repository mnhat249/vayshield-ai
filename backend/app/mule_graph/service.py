"""Đồ thị Mule Pre-SIMO: ghi nhận STK nghi vấn, quorum, gom cụm Dynamic VA (File 4 §2.2, §5.1).

- STK mã hóa envelope (AAD gắn ``mule_account_nodes|account_cipher_envelope|<node_id>``) + blind
  index ``PEPPER_MULE_ACCOUNT`` (S2-EC27, EC-DB09: STK chữ-số hay alias đều là chuỗi A-Z0-9, giữ
  số 0 đầu).
- Cạnh chèn TRƯỚC (``ON CONFLICT DO NOTHING``, 1 phiếu/thiết bị), rồi đếm lại quorum trong cùng
  giao dịch ngắn (EC-DB07). Chỉ ``>= 2`` thiết bị độc lập mới lên ``QUORUM_REACHED`` (S2-EC27);
  dưới ngưỡng, ``threat_confidence`` bị chặn ở mức mặc định 0.35 dù bằng chứng mạnh đến đâu.
- Tài khoản ảo động (EC-DB02): ``va_prefix_hmac`` = blind index của ``<BIN>:VAPFX<6-8 số đầu>``.
  ``>= 2`` STK khác đuôi cùng ``(bank_bin, va_prefix_hmac)`` → cùng 1 cụm; quorum tính trên cả dải.
- Cụm lên ``CRITICAL_WAVE`` khi có ``>= 2`` thiết bị độc lập trong cửa sổ trượt 6 giờ (File 3 §5.2);
  tâm cụm = Huber spherical medoid (EC-AI20).

Không log số tài khoản / SĐT.
"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from app.core.ids import new_uuid7
from app.core.logging import get_logger
from app.core.schemas.ops import EvidenceChannel, ScamTrap, SeverityLevel, SimoDossierStatus
from app.core.schemas.sse import OpsThreatSignal
from app.mule_graph.models import ClusterRecord, MuleEdgeRecord, MuleNodeRecord
from app.mule_graph.repository import MuleGraphRepository
from app.mule_graph.robust import robust_centroid
from app.security.blind_index import BlindIndexer, BlindIndexKind, normalize_mule_account
from app.security.envelope import EnvelopeCipher, record_aad

QUORUM_MIN_DEVICES: Final = 2
CLUSTER_WINDOW: Final = timedelta(hours=6)
DEFAULT_CONFIDENCE: Final = 0.35
MAX_CONFIDENCE: Final = 0.99
EMBEDDING_DIM: Final = 768
VA_MIN_DIGITS: Final = 10
_DIGITS_RE: Final = re.compile(r"\d+")

# Giả định (đặc tả chỉ nói "bằng chứng hợp lệ"/"evidence weighting"): trọng số theo kênh.
EVIDENCE_WEIGHTS: Final[dict[EvidenceChannel, float]] = {
    EvidenceChannel.VIETQR_CRYPTO_DECODED: 0.5,
    EvidenceChannel.OCR_RECEIPT_EXTRACTED: 0.35,
    EvidenceChannel.VOICE_CHAT_REPORTED: 0.2,
}

_log = get_logger("vayshield.mule_graph")

NODES_TABLE: Final = "mule_account_nodes"
CIPHER_COLUMN: Final = "account_cipher_envelope"


def mask_account(account_no: str) -> str:
    """``***`` + 3 ký tự cuối (đủ để vận hành nhận diện, không lộ STK)."""
    return "***" + account_no[-3:]


def va_prefix_of(account_no: str) -> str | None:
    """6–8 chữ số đầu của STK dạng số ≥ 10 chữ số (dải VA); alias chữ/STK ngắn → ``None``."""
    if len(account_no) < VA_MIN_DIGITS or not _DIGITS_RE.fullmatch(account_no):
        return None
    return account_no[: 8 if len(account_no) >= 12 else 6]


def compute_confidence(edges: Sequence[MuleEdgeRecord], quorum: int) -> float:
    """Dưới quorum: chặn ở 0.35 (chống vu khống bởi 1 nguồn). Đủ quorum: noisy-OR trọng số,
    MỖI THIẾT BỊ chỉ góp 1 lần với kênh mạnh nhất (1 thiết bị bơm nhiều cạnh không đẩy điểm lên)."""
    if quorum < QUORUM_MIN_DEVICES:
        return DEFAULT_CONFIDENCE
    best: dict[UUID, float] = {}
    for e in edges:
        w = EVIDENCE_WEIGHTS[e.evidence_channel]
        best[e.reporter_device_id] = max(best.get(e.reporter_device_id, 0.0), w)
    miss = 1.0
    for w in best.values():
        miss *= 1.0 - w
    return min(MAX_CONFIDENCE, max(0.5, round(1.0 - miss, 4)))


@dataclass(frozen=True, slots=True)
class ReportResult:
    node_id: UUID
    newly_counted: bool
    distinct_device_quorum: int
    status: SimoDossierStatus
    threat_confidence: float
    cluster_id: UUID | None
    is_dynamic_va_range: bool
    cluster_distinct_devices: int = 0
    cluster_severity: SeverityLevel | None = None


ThreatSignalSink = Callable[[OpsThreatSignal], Awaitable[object]]
"""Cổng tùy chọn: nhận tín hiệu đã che (chỉ ``masked_account_display``) khi đạt quorum/leo thang."""


class MuleGraphService:
    def __init__(
        self,
        repo: MuleGraphRepository,
        indexer: BlindIndexer,
        cipher: EnvelopeCipher,
        *,
        pepper_version: int = 1,
        on_threat_signal: ThreatSignalSink | None = None,
    ) -> None:
        self._repo = repo
        self._indexer = indexer
        self._cipher = cipher
        self._pepper_version = pepper_version
        self._on_threat_signal = on_threat_signal

    async def report_mule_account(
        self,
        *,
        bank_bin: str,
        account_no: str,
        reporter_device_id: UUID,
        channel: EvidenceChannel,
        now: datetime,
        scam_script_vec: tuple[float, ...] | None = None,
        scam_trap: ScamTrap = ScamTrap.TRAP_1_ADVANCE_FEE,
        impersonated_lender_id: UUID | None = None,
    ) -> ReportResult:
        """Ghi nhận 1 báo cáo STK nghi vấn từ 1 thiết bị (idempotent theo thiết bị)."""
        if scam_script_vec is not None and len(scam_script_vec) != EMBEDDING_DIM:
            raise ValueError(f"scam_script_vec phải có {EMBEDDING_DIM} chiều")
        if scam_script_vec is not None and not (
            all(math.isfinite(x) for x in scam_script_vec) and any(scam_script_vec)
        ):
            raise ValueError("scam_script_vec phải hữu hạn và khác vector 0")
        canonical = normalize_mule_account(bank_bin, account_no)  # EC-DB09 (A-Z0-9, giữ số 0)
        bin_clean, acct = canonical.split(":", 1)
        lookup = self._indexer.lookup(BlindIndexKind.MULE_ACCOUNT, canonical)
        prefix = va_prefix_of(acct)
        va_lookup = (
            self._indexer.lookup(BlindIndexKind.MULE_ACCOUNT, f"{bin_clean}:VAPFX{prefix}")
            if prefix is not None
            else None
        )
        va_hmac = va_lookup.current if va_lookup is not None else None
        va_candidates = va_lookup.candidates if va_lookup is not None else ()
        async with self._repo.transaction():
            await self._repo.lock_scope(va_hmac or lookup.current)
            node = await self._repo.find_node_by_hmacs(lookup.candidates)
            prev_status = node.simo_dossier_status if node is not None else None
            if node is None:
                node_id = new_uuid7()
                blob = self._cipher.encrypt(
                    canonical.encode("utf-8"), aad=record_aad(NODES_TABLE, CIPHER_COLUMN, node_id)
                )
                node = await self._repo.create_node(
                    MuleNodeRecord(
                        id=node_id,
                        bank_bin=bin_clean,
                        account_hmac=lookup.current,
                        va_prefix_hmac=va_hmac,
                        pepper_version=lookup.current_version,
                        masked_account_display=mask_account(acct),
                        account_cipher_envelope=blob,
                        distinct_device_quorum=0,
                        first_reported_at=now,
                        last_reported_at=now,
                        is_dynamic_va_range=False,
                    )
                )
            elif lookup.needs_rehash(node.account_hmac, node.pepper_version):
                node.account_hmac = lookup.current  # nâng cấp lười pepper (EC-DB08)
                node.pepper_version = lookup.current_version
            node.last_reported_at = now
            if va_hmac is not None:
                node.va_prefix_hmac = va_hmac  # gán/nâng cấp lười pepper cho dải VA (EC-DB08)
            await self._repo.save_node(node)

            inserted = await self._repo.add_edge(
                MuleEdgeRecord(
                    mule_node_id=node.id,
                    reporter_device_id=reporter_device_id,
                    evidence_channel=channel,
                    created_at=now,
                    scam_script_vec=scam_script_vec,
                )
            )
            await self._repo.refresh_quorum(node.id)
            cluster_info = await self._rebuild(
                node.id, now, scam_trap, impersonated_lender_id, va_hmac, va_candidates
            )
            final = await self._node(node.id)
        if self._on_threat_signal is not None:
            await self._emit_signals(self._on_threat_signal, final, prev_status, cluster_info, now)
        return ReportResult(
            node_id=final.id,
            newly_counted=inserted,
            distinct_device_quorum=final.distinct_device_quorum,
            status=final.simo_dossier_status,
            threat_confidence=final.threat_confidence,
            cluster_id=final.cluster_id,
            is_dynamic_va_range=final.is_dynamic_va_range,
            cluster_distinct_devices=cluster_info[0],
            cluster_severity=cluster_info[1],
        )

    async def _rebuild(
        self,
        node_id: UUID,
        now: datetime,
        scam_trap: ScamTrap,
        lender_id: UUID | None,
        va_hmac: str | None,
        va_candidates: Sequence[str],
    ) -> tuple[int, SeverityLevel | None, bool]:
        """Tính lại trạng thái đỉnh + dải VA + cụm (trong giao dịch của caller).

        Quorum của MỖI STK chỉ tính từ thiết bị đã báo chính STK đó. Tín hiệu dải VA (nhiều STK
        cùng ``(bank_bin, va_prefix_hmac)``) chỉ gom cụm và leo thang ở MỨC CỤM, không lan
        ``QUORUM_REACHED``/độ tin cậy xuống STK chưa đủ quorum riêng (tránh gán nhầm STK hợp lệ
        chỉ vì trùng 6–8 số đầu).
        """
        node = await self._node(node_id)
        group = [node]
        if va_hmac is not None:
            group = await self._repo.find_nodes_by_va(node.bank_bin, va_candidates)
        in_range = len(group) >= QUORUM_MIN_DEVICES  # >= 2 STK khác đuôi cùng dải (EC-DB02)
        edges = await self._repo.edges_for_nodes([n.id for n in group])
        cluster_id: UUID | None = None
        cluster_info: tuple[int, SeverityLevel | None, bool] = (0, None, False)
        if in_range:
            cluster, escalated = await self._ensure_cluster(group, edges, now, scam_trap, lender_id)
            cluster_id = cluster.id
            cluster_info = (
                len({e.reporter_device_id for e in edges}),
                cluster.severity_level,
                escalated,
            )
        for n in group:
            own = [e for e in edges if e.mule_node_id == n.id]
            quorum = len({e.reporter_device_id for e in own})
            if n.simo_dossier_status in (
                SimoDossierStatus.OBSERVING,
                SimoDossierStatus.QUORUM_REACHED,
            ):
                n.simo_dossier_status = (
                    SimoDossierStatus.QUORUM_REACHED
                    if quorum >= QUORUM_MIN_DEVICES
                    else SimoDossierStatus.OBSERVING
                )
                n.threat_confidence = compute_confidence(own, quorum)
            n.distinct_device_quorum = quorum
            if va_hmac is not None:
                n.va_prefix_hmac = va_hmac
            if in_range:
                n.is_dynamic_va_range = True
                n.cluster_id = cluster_id
            await self._repo.save_node(n)
        return cluster_info

    async def _node(self, node_id: UUID) -> MuleNodeRecord:
        found = await self._repo.get_node(node_id)
        if found is None:
            raise RuntimeError("mule node biến mất trong giao dịch")
        return found

    async def _ensure_cluster(
        self,
        group: list[MuleNodeRecord],
        edges: list[MuleEdgeRecord],
        now: datetime,
        scam_trap: ScamTrap,
        lender_id: UUID | None,
    ) -> tuple[ClusterRecord, bool]:
        """Trả (cụm, vừa leo lên CRITICAL_WAVE ở lần gọi này)."""
        existing_id = next((n.cluster_id for n in group if n.cluster_id is not None), None)
        cluster = await self._repo.get_cluster(existing_id) if existing_id else None
        if cluster is None:
            first = group[0]
            cluster = ClusterRecord(
                id=new_uuid7(),
                campaign_label=f"VA-RANGE {first.bank_bin}/{(first.va_prefix_hmac or '')[:8]}",
                dominant_scam_trap=scam_trap,
                severity_level=SeverityLevel.WATCH,
                first_seen_at=now,
                last_seen_at=now,
                impersonated_lender_id=lender_id,
            )
        was_critical = cluster.severity_level is SeverityLevel.CRITICAL_WAVE
        cluster.last_seen_at = now
        if cluster.impersonated_lender_id is None:
            cluster.impersonated_lender_id = lender_id
        window_start = now - CLUSTER_WINDOW
        recent = {e.reporter_device_id for e in edges if e.created_at >= window_start}
        cluster.severity_level = (
            SeverityLevel.CRITICAL_WAVE
            if len(recent) >= QUORUM_MIN_DEVICES
            else SeverityLevel.WATCH
        )
        day_start = now - timedelta(hours=24)
        cluster.incident_count_24h = max(1, sum(1 for e in edges if e.created_at >= day_start))
        vecs = [e.scam_script_vec for e in edges if e.scam_script_vec is not None]
        if vecs:
            usable = [v for v in vecs if all(math.isfinite(x) for x in v) and any(v)]
            if usable:
                cluster.centroid_embedding = robust_centroid(usable)
        await self._repo.save_cluster(cluster)
        return cluster, (cluster.severity_level is SeverityLevel.CRITICAL_WAVE and not was_critical)

    @staticmethod
    async def _emit_signals(
        sink: ThreatSignalSink,
        node: MuleNodeRecord,
        prev_status: SimoDossierStatus | None,
        cluster_info: tuple[int, SeverityLevel | None, bool],
        now: datetime,
    ) -> None:
        """Best-effort, sau commit: lỗi phát tín hiệu KHÔNG làm hỏng báo cáo mule.

        Chỉ phát khi VỪA chuyển trạng thái (không lặp lại mỗi báo cáo). Tín hiệu chỉ chứa STK đã
        che + mẫu tóm tắt cứng (không có STK thô)."""
        signals: list[OpsThreatSignal] = []
        if (
            node.simo_dossier_status is SimoDossierStatus.QUORUM_REACHED
            and prev_status is not SimoDossierStatus.QUORUM_REACHED
        ):
            severity = cluster_info[1].value if cluster_info[1] is not None else "ELEVATED"
            signals.append(
                OpsThreatSignal(
                    signal_id=new_uuid7(),
                    kind="QUORUM_REACHED",
                    cluster_id=node.cluster_id,
                    mule_node_id=node.id,
                    bank_bin=node.bank_bin,
                    masked_account_display=node.masked_account_display,
                    severity_level=severity,  # type: ignore[arg-type]
                    occurred_at=now,
                    summary_vi=(
                        f"Tài khoản {node.masked_account_display} đạt quorum "
                        f"{node.distinct_device_quorum} thiết bị độc lập báo cáo."
                    ),
                )
            )
        if cluster_info[2] and node.cluster_id is not None:
            signals.append(
                OpsThreatSignal(
                    signal_id=new_uuid7(),
                    kind="CLUSTER_ESCALATED",
                    cluster_id=node.cluster_id,
                    mule_node_id=node.id,
                    bank_bin=node.bank_bin,
                    masked_account_display=node.masked_account_display,
                    severity_level="CRITICAL_WAVE",
                    occurred_at=now,
                    summary_vi=(
                        "Cụm tài khoản ảo động leo thang CRITICAL_WAVE "
                        f"({cluster_info[0]} thiết bị trong cửa sổ 6 giờ)."
                    ),
                )
            )
        for signal in signals:
            try:
                await sink(signal)
            except Exception:
                _log.warning("mule_threat_signal_publish_failed", extra={"kind": signal.kind})
