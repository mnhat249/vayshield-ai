"""Chạy seed idempotent: ``python -m seeds.run`` (cần ADMIN_DATABASE_URL, đã migrate 0001+0002).

Mọi câu lệnh dùng ``ON CONFLICT`` nên chạy lại nhiều lần không nhân bản dữ liệu.
Embedding là vector giả xác định (băm SHA-256) - chỉ để thỏa NOT NULL; pipeline RAG (T09)
phải tính lại embedding thật khi có Gemini.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sys
from datetime import date

import asyncpg  # type: ignore[import-untyped]

from seeds.data import (
    CLUSTERS,
    EDGES,
    LENDERS,
    MAX_QUORUM,
    P2P_SANDBOX_CAP_VND,
    PROVISIONS,
    SANDBOX_NAMESPACE,
    device_id,
    dossier_status,
)

DIM = 768


def placeholder_embedding(text: str) -> list[float]:
    """Vector 768 chiều xác định, chuẩn hóa L2 (KHÔNG phải embedding ngữ nghĩa)."""
    raw: list[float] = []
    counter = 0
    while len(raw) < DIM:
        block = hashlib.sha256(f"{text}|{counter}".encode()).digest()
        raw.extend((b - 127.5) / 127.5 for b in block)
        counter += 1
    vec = raw[:DIM]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


async def seed(conn: asyncpg.Connection) -> None:
    async with conn.transaction():
        for ln in LENDERS:
            cap = ln.sandbox_cap_vnd
            if ln.legal_category == "P2P_SANDBOX" and cap is None:
                cap = P2P_SANDBOX_CAP_VND
            await conn.execute(
                """INSERT INTO licensed_lender_registry
                (entity_code, legal_category, official_short_name, official_legal_name,
                 normalized_name, official_domains, official_hotlines, android_packages,
                 sandbox_cap_vnd, aliases, verified)
                VALUES ($1,$2,$3,'',$4,$5,$6,$7::jsonb,$8,$9,$10)
                ON CONFLICT (entity_code) DO UPDATE SET
                  legal_category=EXCLUDED.legal_category,
                  official_short_name=EXCLUDED.official_short_name,
                  normalized_name=EXCLUDED.normalized_name,
                  official_domains=EXCLUDED.official_domains,
                  official_hotlines=EXCLUDED.official_hotlines,
                  android_packages=EXCLUDED.android_packages,
                  sandbox_cap_vnd=EXCLUDED.sandbox_cap_vnd,
                  aliases=EXCLUDED.aliases, verified=EXCLUDED.verified, updated_at=NOW()""",
                ln.entity_code, ln.legal_category, ln.short_name, ln.normalized,
                list(ln.domains), list(ln.hotlines), json.dumps(list(ln.android_packages)),
                cap, list(ln.aliases), ln.verified,
            )  # fmt: skip
        for p in PROVISIONS:
            emb = vector_literal(placeholder_embedding(p.clause_code + p.title_vi))
            await conn.execute(
                """INSERT INTO legal_provision_nodes
                (clause_code, doc_type, doc_number, effective_date, title_vi, verbatim_content,
                 plain_summary_vi, applicable_verdicts, embedding_768, verified)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::vector,$10)
                ON CONFLICT (clause_code) DO UPDATE SET
                  doc_type=EXCLUDED.doc_type, doc_number=EXCLUDED.doc_number,
                  effective_date=EXCLUDED.effective_date, title_vi=EXCLUDED.title_vi,
                  verbatim_content=EXCLUDED.verbatim_content,
                  plain_summary_vi=EXCLUDED.plain_summary_vi,
                  applicable_verdicts=EXCLUDED.applicable_verdicts,
                  embedding_768=EXCLUDED.embedding_768, verified=EXCLUDED.verified""",
                p.clause_code, p.doc_type, p.doc_number, date.fromisoformat(p.effective_date),
                p.title_vi, p.verbatim_content, p.plain_summary_vi,
                list(p.applicable_verdicts), emb, p.verified,
            )  # fmt: skip
        for src, dst, rel in EDGES:
            await conn.execute(
                """INSERT INTO legal_provision_edges (source_clause, target_clause, relation_type)
                VALUES ($1,$2,$3) ON CONFLICT DO NOTHING""",
                src, dst, rel,
            )  # fmt: skip
        for n in range(1, MAX_QUORUM + 1):
            await conn.execute(
                """INSERT INTO anonymous_devices (id, platform_type)
                VALUES ($1, 'JUDGE_SANDBOX') ON CONFLICT (id) DO NOTHING""",
                device_id(n),
            )
        for cl in CLUSTERS:
            await conn.execute(
                """INSERT INTO fraud_campaign_clusters
                (id, campaign_label, impersonated_lender_id, dominant_scam_trap,
                 incident_count_24h, severity_level, sandbox_namespace)
                VALUES ($1,$2,(SELECT id FROM licensed_lender_registry WHERE entity_code=$3),
                        $4,$5,$6,$7)
                ON CONFLICT (id) DO UPDATE SET
                  campaign_label=EXCLUDED.campaign_label,
                  impersonated_lender_id=EXCLUDED.impersonated_lender_id,
                  dominant_scam_trap=EXCLUDED.dominant_scam_trap,
                  incident_count_24h=EXCLUDED.incident_count_24h,
                  severity_level=EXCLUDED.severity_level,
                  sandbox_namespace=EXCLUDED.sandbox_namespace""",
                cl.cluster_id, cl.label, cl.impersonated_entity, cl.trap,
                cl.incidents_24h, cl.severity, SANDBOX_NAMESPACE,
            )  # fmt: skip
            for a in cl.accounts:
                await conn.execute(
                    """INSERT INTO mule_account_nodes
                    (id, bank_bin, account_hmac_sha256, masked_account_display,
                     account_cipher_envelope, distinct_device_quorum, threat_confidence,
                     simo_dossier_status, cluster_id, sandbox_namespace)
                    VALUES ($1,'SANDBOX',$2,$3,$4,$5,$6,$9,$7,$8)
                    ON CONFLICT (account_hmac_sha256) DO UPDATE SET
                      distinct_device_quorum=EXCLUDED.distinct_device_quorum,
                      threat_confidence=EXCLUDED.threat_confidence,
                      simo_dossier_status=EXCLUDED.simo_dossier_status,
                      cluster_id=EXCLUDED.cluster_id,
                      sandbox_namespace=EXCLUDED.sandbox_namespace""",
                    a.node_id, a.hmac, a.masked_display, b"sandbox_seed:no-real-account",
                    a.quorum, a.confidence, cl.cluster_id, SANDBOX_NAMESPACE,
                    dossier_status(a.quorum),
                )  # fmt: skip
        # EC-DB07: cạnh trước, rồi quorum = COUNT(cạnh) trong cùng transaction.
        for cl in CLUSTERS:
            for a in cl.accounts:
                for n in range(1, a.quorum + 1):
                    await conn.execute(
                        """INSERT INTO mule_incident_edges
                        (mule_node_id, reporter_device_id, evidence_channel)
                        VALUES ($1,$2,'VIETQR_CRYPTO_DECODED')
                        ON CONFLICT (mule_node_id, reporter_device_id) DO NOTHING""",
                        a.node_id, device_id(n),
                    )  # fmt: skip
                await conn.execute(
                    """UPDATE mule_account_nodes SET distinct_device_quorum =
                    (SELECT COUNT(*) FROM mule_incident_edges WHERE mule_node_id = $1)
                    WHERE id = $1""",
                    a.node_id,
                )


async def main_async(url: str) -> None:
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        await seed(conn)
    finally:
        await conn.close()


def main() -> int:
    url = os.environ.get("ADMIN_DATABASE_URL") or os.environ.get("TEST_ADMIN_DATABASE_URL")
    if not url:
        print("Thiếu ADMIN_DATABASE_URL", file=sys.stderr)
        return 2
    asyncio.run(main_async(url))
    print("seed OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
