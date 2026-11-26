"""Test seed T08: số lượng, ràng buộc, không bịa dữ liệu, idempotent (pg, chạy trên CI)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.verdict_codes import VerdictCode
from app.db_schema.migrate import run_migrations
from seeds import data
from seeds.run import DIM, placeholder_embedding, seed

MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0002_seed_support.sql"
RELATIONS = {"EXEMPTS_FOR_CREDIT_INST", "CRIMINALIZES_EXCESS_OF", "AMENDS", "GUIDES_IMPLEMENTATION"}
REQUIRED = {
    "BLHS_2015_D174",
    "BLHS_2015_D201",
    "NQ_01_2021_HDTP",
    "BLDS_2015_D468",
    "TCTD_2024_D15",
    "TCTD_2024_D100",
    "TT_43_2016_NHNN",
    "TT_18_2019_NHNN",
    "ND_94_2025",
}


def test_lender_entity_codes_unique_and_categories_valid() -> None:
    codes = [x.entity_code for x in data.LENDERS]
    assert len(codes) == len(set(codes))
    assert {x.legal_category for x in data.LENDERS} <= {
        "COMMERCIAL_BANK",
        "LICENSED_FINCO",
        "P2P_SANDBOX",
        "LICENSED_PAWN_CHAIN",
        "BNPL_WALLET_PROVIDER",
    }
    assert sum(x.legal_category == "COMMERCIAL_BANK" for x in data.LENDERS) >= 5
    assert sum(x.legal_category == "LICENSED_FINCO" for x in data.LENDERS) >= 10


def test_no_fabricated_official_data_all_unverified() -> None:
    """Không bịa: package/cert/domain rỗng; chưa kiểm chứng thì verified=False."""
    for x in data.LENDERS:
        assert x.verified is False
        assert x.android_packages == () and x.domains == ()
    hotlines = {h for x in data.LENDERS for h in x.hotlines}
    assert hotlines <= {"1900 633 633"}  # chỉ ví dụ trong đặc tả File 4
    for p in data.PROVISIONS:
        assert p.verified is False


def test_normalize_name_strips_diacritics() -> None:
    assert data.normalize_name("  Đầu Tư  Việt ") == "dau tu viet"


def test_provision_codes_match_t09_convention() -> None:
    codes = {p.clause_code for p in data.PROVISIONS}
    assert codes >= REQUIRED
    assert not any(c.startswith("LUAT_") for c in codes)
    assert len(codes) == len(data.PROVISIONS)


def test_provision_verdicts_are_valid_codes() -> None:
    valid = {v.value for v in VerdictCode}
    for p in data.PROVISIONS:
        assert set(p.applicable_verdicts) <= valid, p.clause_code
        assert p.applicable_verdicts


def test_edges_reference_existing_nodes_and_valid_relations() -> None:
    codes = {p.clause_code for p in data.PROVISIONS}
    assert len(set(data.EDGES)) == len(data.EDGES)
    for src, dst, rel in data.EDGES:
        assert src in codes and dst in codes and src != dst
        assert rel in RELATIONS
    # cạnh ngoại lệ File 3 §5.1: BLDS 468 <-> TCTD Đ100
    assert ("TCTD_2024_D100", "BLDS_2015_D468", "EXEMPTS_FOR_CREDIT_INST") in data.EDGES


def test_ec_ops16_three_labeled_sandbox_clusters() -> None:
    """EC-OPS16: đúng 3 cụm mô phỏng có nhãn, namespace sandbox_seed, tài khoản không thật."""
    assert len(data.CLUSTERS) == 3
    assert data.SANDBOX_NAMESPACE == "sandbox_seed"
    entities = {x.entity_code for x in data.LENDERS}
    hmacs: set[str] = set()
    for c in data.CLUSTERS:
        assert c.label.startswith("[Dữ liệu Mô phỏng Thực tiễn]")
        assert c.severity in {"WATCH", "ELEVATED", "CRITICAL_WAVE"}
        assert c.impersonated_entity is None or c.impersonated_entity in entities
        assert c.accounts
        for a in c.accounts:
            assert re.fullmatch(r"[0-9a-f]{64}", a.hmac)
            assert 0.0 <= a.confidence <= 1.0
            hmacs.add(a.hmac)
    assert len(hmacs) == sum(len(c.accounts) for c in data.CLUSTERS)


def test_placeholder_embedding_deterministic_unit_768() -> None:
    a = placeholder_embedding("x")
    assert len(a) == DIM == 768
    assert a == placeholder_embedding("x") and a != placeholder_embedding("y")
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9


def test_migration_0002_is_additive_and_idempotent() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "aliases" in sql and "verified" in sql and "sandbox_namespace" in sql
    assert not re.search(r"\bDROP\b|\bDELETE\b|\bTRUNCATE\b", sql, re.I)
    for line in re.findall(r"ALTER TABLE.*", sql):
        assert "IF NOT EXISTS" in line


@pytest.mark.pg
async def test_seed_idempotent_counts_pg(pg_admin: AsyncEngine, pg: AsyncEngine) -> None:
    """EC-OPS16 trên PG thật: seed 2 lần -> số dòng không đổi; đồ thị không trống."""
    from tests.conftest import TEST_ADMIN_DATABASE_URL

    assert TEST_ADMIN_DATABASE_URL
    await run_migrations(TEST_ADMIN_DATABASE_URL)
    import asyncpg  # type: ignore[import-untyped]

    conn = await asyncpg.connect(TEST_ADMIN_DATABASE_URL.replace("+asyncpg", "", 1))
    try:
        await seed(conn)
        await seed(conn)
    finally:
        await conn.close()
    async with pg_admin.connect() as c:
        n_l = (await c.execute(text("SELECT count(*) FROM licensed_lender_registry"))).scalar_one()
        n_p = (await c.execute(text("SELECT count(*) FROM legal_provision_nodes"))).scalar_one()
        n_e = (await c.execute(text("SELECT count(*) FROM legal_provision_edges"))).scalar_one()
        n_m = (
            await c.execute(
                text(
                    "SELECT count(*) FROM mule_account_nodes WHERE sandbox_namespace='sandbox_seed'"
                )
            )
        ).scalar_one()
        n_c = (
            await c.execute(
                text(
                    "SELECT count(*) FROM fraud_campaign_clusters"
                    " WHERE sandbox_namespace='sandbox_seed'"
                )
            )
        ).scalar_one()
    assert n_l >= len(data.LENDERS) and n_p >= len(data.PROVISIONS) and n_e >= len(data.EDGES)
    assert n_c == 3
    assert n_m == sum(len(c.accounts) for c in data.CLUSTERS)
