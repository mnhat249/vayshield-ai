"""Nghiệp vụ bàn làm việc Fraud Ops: danh sách cụm + hồ sơ SIMO (File 3 §5.2, File 4 §4.1).

Nguyên tắc dữ liệu:
- Hồ sơ KHÔNG chứa số tài khoản thô, không chứa định danh thiết bị báo cáo, không chứa văn bản tự do
  từ ảnh/người dùng. ``masked_snippet_vi`` do mẫu cứng sinh từ kênh bằng chứng + STK đã che.
- Kênh bằng chứng và ``scam_script_vec`` đọc từ cạnh do pipeline phía máy chủ ghi (T14); không
  có đường nào để client chỉ định kênh/vector.
- ``unseal=true``: GHI chuỗi băm kiểm toán TRƯỚC, rồi mới giải mã. Ghi chuỗi lỗi -> 503, không lộ.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from app.copilot.advisory import AdvisoryContext, AdvisoryWriter
from app.copilot.repository import (
    ClusterCursor,
    OpsRepository,
    decode_cursor,
    encode_cursor,
)
from app.core.errors import AppError, ErrorCode
from app.core.ids import new_uuid7
from app.core.schemas.ops import (
    DossierEvidence,
    EvidenceChannel,
    MuleCluster,
    MuleClusterPage,
    MuleLink,
    MuleNode,
    ScamTrap,
    SeverityLevel,
    SimoDossier,
)
from app.mule_graph.models import ClusterRecord, MuleEdgeRecord, MuleNodeRecord
from app.mule_graph.service import CIPHER_COLUMN, NODES_TABLE
from app.security.envelope import EnvelopeCipher, EnvelopeDecryptionError, record_aad

SIMULATED_SEED_PREFIX: Final = "[Dữ liệu Mô phỏng Thực tiễn]"  # EC-OPS16
UNSEAL_ACTION: Final = "SIMO_DOSSIER_UNSEAL"
MAX_NODES_PER_CLUSTER: Final = 100
MAX_LINKS_PER_CLUSTER: Final = 300
MAX_EVIDENCE: Final = 100
SCRIPT_SIMILARITY_MIN: Final = 0.85
_Relation = Literal["SAME_VA_PREFIX", "SHARED_SCAM_SCRIPT", "SHARED_REPORTER_WAVE"]

_SNIPPET: Final[dict[EvidenceChannel, str]] = {
    EvidenceChannel.VIETQR_CRYPTO_DECODED: (
        "Mã VietQR giải mã được từ ảnh người dùng gửi, tài khoản nhận {acct}."
    ),
    EvidenceChannel.OCR_RECEIPT_EXTRACTED: (
        "Biên lai được đọc bằng OCR (thông tin nạn nhân đã làm mờ), tài khoản nhận {acct}."
    ),
    EvidenceChannel.VOICE_CHAT_REPORTED: (
        "Người dùng báo qua giọng nói/trò chuyện, tài khoản nhận {acct}."
    ),
}


def _unit(vec: Sequence[float]) -> list[float] | None:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm > 0 else None


def _mean_script(edges: Sequence[MuleEdgeRecord]) -> list[float] | None:
    vecs = [u for e in edges if e.scam_script_vec and (u := _unit(e.scam_script_vec))]
    if not vecs:
        return None
    dim = len(vecs[0])
    acc = [0.0] * dim
    for v in vecs:
        if len(v) != dim:
            continue
        for i, x in enumerate(v):
            acc[i] += x
    return _unit(acc)


def _pair(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    return (a, b) if str(a) <= str(b) else (b, a)


def build_links(nodes: Sequence[MuleNodeRecord], edges: Sequence[MuleEdgeRecord]) -> list[MuleLink]:
    """Quan hệ giữa các đỉnh trong cụm (suy ra từ dữ liệu máy chủ). Tối đa ``MAX_LINKS``."""
    seen: set[tuple[_Relation, UUID, UUID]] = set()
    links: list[MuleLink] = []

    def add(rel: _Relation, a: UUID, b: UUID) -> None:
        if a == b or len(links) >= MAX_LINKS_PER_CLUSTER:
            return
        x, y = _pair(a, b)
        if (rel, x, y) in seen:
            return
        seen.add((rel, x, y))
        links.append(MuleLink(source_node_id=x, target_node_id=y, relation=rel))

    by_va: dict[tuple[str, str], list[UUID]] = defaultdict(list)
    for n in nodes:
        if n.va_prefix_hmac:
            by_va[(n.bank_bin, n.va_prefix_hmac)].append(n.id)
    for group in by_va.values():
        for other in group[1:]:
            add("SAME_VA_PREFIX", group[0], other)

    by_device: dict[UUID, set[UUID]] = defaultdict(set)
    edges_by_node: dict[UUID, list[MuleEdgeRecord]] = defaultdict(list)
    for e in edges:
        by_device[e.reporter_device_id].add(e.mule_node_id)
        edges_by_node[e.mule_node_id].append(e)
    for node_ids in by_device.values():
        ordered = sorted(node_ids, key=str)
        for other in ordered[1:]:
            add("SHARED_REPORTER_WAVE", ordered[0], other)

    scripts = {nid: v for nid, es in edges_by_node.items() if (v := _mean_script(es)) is not None}
    ids = sorted(scripts, key=str)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if len(scripts[a]) == len(scripts[b]):
                cos = sum(x * y for x, y in zip(scripts[a], scripts[b], strict=True))
                if cos >= SCRIPT_SIMILARITY_MIN:
                    add("SHARED_SCAM_SCRIPT", a, b)
    return links


def _node_dto(n: MuleNodeRecord) -> MuleNode:
    return MuleNode(
        node_id=n.id,
        bank_bin=n.bank_bin,
        masked_account_display=n.masked_account_display,
        distinct_device_quorum=n.distinct_device_quorum,
        threat_confidence=n.threat_confidence,
        simo_dossier_status=n.simo_dossier_status,
        is_dynamic_va_range=n.is_dynamic_va_range,
    )


async def list_clusters(
    repo: OpsRepository,
    *,
    severity: SeverityLevel | None,
    limit: int,
    cursor: str | None,
) -> MuleClusterPage:
    after: ClusterCursor | None = decode_cursor(cursor) if cursor else None
    rows = await repo.list_clusters(severity=severity, limit=limit + 1, after=after)
    page, has_more = rows[:limit], len(rows) > limit
    lenders = await repo.lender_names(
        [c.impersonated_lender_id for c in page if c.impersonated_lender_id is not None]
    )
    items: list[MuleCluster] = []
    for c in page:
        nodes = (await repo.graph.nodes_in_cluster(c.id))[:MAX_NODES_PER_CLUSTER]
        edges = await repo.graph.edges_for_nodes([n.id for n in nodes]) if nodes else []
        items.append(
            MuleCluster(
                cluster_id=c.id,
                campaign_label=c.campaign_label,
                impersonated_lender=lenders.get(c.impersonated_lender_id)
                if c.impersonated_lender_id
                else None,
                dominant_scam_trap=c.dominant_scam_trap,
                severity_level=c.severity_level,
                is_simulated_seed=c.campaign_label.startswith(SIMULATED_SEED_PREFIX),
                first_seen_at=c.first_seen_at,
                last_seen_at=c.last_seen_at,
                nodes=[_node_dto(n) for n in sorted(nodes, key=lambda n: str(n.id))],
                links=build_links(nodes, edges),
            )
        )
    next_cursor = (
        encode_cursor(ClusterCursor(page[-1].last_seen_at, page[-1].id))
        if has_more and page
        else None
    )
    return MuleClusterPage(items=items, next_cursor=next_cursor)


def _evidence(node: MuleNodeRecord, edges: Sequence[MuleEdgeRecord]) -> list[DossierEvidence]:
    ordered = sorted(edges, key=lambda e: e.created_at)[:MAX_EVIDENCE]
    return [
        DossierEvidence(
            channel=e.evidence_channel,
            reported_at=e.created_at,
            masked_snippet_vi=_SNIPPET[e.evidence_channel].format(acct=node.masked_account_display),
        )
        for e in ordered
    ]


def _is_seed_cluster(cluster: ClusterRecord | None) -> bool:
    return cluster is not None and cluster.campaign_label.startswith(SIMULATED_SEED_PREFIX)


def _actor(subject: str) -> str:
    return subject.replace("|", "_")[:120]


async def build_dossier(
    repo: OpsRepository,
    cipher: EnvelopeCipher | None,
    writer: AdvisoryWriter,
    *,
    mule_node_id: UUID,
    unseal: bool,
    actor_subject: str,
    now: datetime,
    is_judge: bool = False,
) -> SimoDossier:
    node = await repo.graph.get_node(mule_node_id)
    if node is None:
        raise AppError(ErrorCode.NOT_FOUND, 404, "Không tìm thấy tài khoản mồi.")
    edges = await repo.graph.edges_for_nodes([node.id])
    cluster: ClusterRecord | None = (
        await repo.graph.get_cluster(node.cluster_id) if node.cluster_id else None
    )
    advisory: str | None = None
    if cluster is not None:
        lender = (
            (await repo.lender_names([cluster.impersonated_lender_id])).get(
                cluster.impersonated_lender_id
            )
            if cluster.impersonated_lender_id
            else None
        )
        draft = await writer.draft(
            AdvisoryContext(
                trap=cluster.dominant_scam_trap
                if isinstance(cluster.dominant_scam_trap, ScamTrap)
                else ScamTrap.TRAP_1_ADVANCE_FEE,
                severity=cluster.severity_level,
                lender_name=lender,
                incident_count_24h=cluster.incident_count_24h,
            )
        )
        advisory = draft.text

    unsealed: str | None = None
    chain_hash: str | None = None
    if unseal and is_judge and not _is_seed_cluster(cluster):
        # Giám khảo (tài khoản dùng chung) chỉ được mở niêm phong dữ liệu mô phỏng, không bao giờ
        # STK thật; chặn TRƯỚC audit/giải mã.
        raise AppError(ErrorCode.FORBIDDEN, 403)
    if unseal:
        if cipher is None:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503, "Kho khóa giải mã chưa được cấu hình.")
        try:  # kiểm toán TRƯỚC khi giải mã: không có dấu vết thì không có số tài khoản
            chain_hash = await repo.append_audit(_actor(actor_subject), UNSEAL_ACTION, node.id, now)
        except Exception:
            raise AppError(
                ErrorCode.SERVICE_DEGRADED,
                503,
                "Không ghi được nhật ký kiểm toán; từ chối mở niêm phong.",
            ) from None
        try:
            plain = cipher.decrypt(
                node.account_cipher_envelope,
                aad=record_aad(NODES_TABLE, CIPHER_COLUMN, node.id),
            ).decode("utf-8")
        except EnvelopeDecryptionError:
            raise AppError(ErrorCode.INTERNAL_ERROR, 500) from None
        unsealed = plain.split(":", 1)[-1]  # "<BIN>:<số tài khoản>" -> số tài khoản

    return SimoDossier(
        dossier_id=new_uuid7(),
        mule_node_id=node.id,
        bank_bin=node.bank_bin,
        masked_account_display=node.masked_account_display,
        account_holder_masked=None,
        unsealed_account_number=unsealed,
        simo_dossier_status=node.simo_dossier_status,
        distinct_device_quorum=node.distinct_device_quorum,
        cluster_label=cluster.campaign_label if cluster else None,
        evidence=_evidence(node, edges),
        advisory_draft_vi=advisory,
        generated_at=now,
        audit_chain_entry_hash=chain_hash,
    )
