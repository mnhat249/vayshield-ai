# ruff: noqa: E501
"""Test tĩnh (không cần DB): parse migration 0001 đối chiếu File 4 §2.2."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SQL_PATH = Path(__file__).resolve().parents[2] / "migrations" / "0001_core_schema.sql"
SQL = SQL_PATH.read_text(encoding="utf-8")

TABLES = [
    "licensed_lender_registry",
    "anonymous_devices",
    "family_guardian_links",
    "protection_sessions",
    "contract_audits",
    "interception_events",
    "legal_provision_nodes",
    "legal_provision_edges",
    "fraud_campaign_clusters",
    "mule_account_nodes",
    "mule_incident_edges",
    "fintech_partners",
    "b2b_disbursement_shields",
    "tamper_evident_audit_chain",
]

INDEXES = [
    "idx_lender_trgm_name",
    "idx_family_active_borrower",
    "idx_sessions_active_shield",
    "idx_legal_hnsw_vec",
    "idx_legal_gin_tsv",
    "idx_mule_va_prefix",
]


def test_has_14_tables() -> None:
    created = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SQL)
    assert sorted(created) == sorted(TABLES)


@pytest.mark.parametrize("ext", ["pgcrypto", "vector", "pg_trgm", "unaccent"])
def test_extensions(ext: str) -> None:
    assert f'CREATE EXTENSION IF NOT EXISTS "{ext}"' in SQL


@pytest.mark.parametrize("name", INDEXES)
def test_indexes_present(name: str) -> None:
    assert f"CREATE INDEX IF NOT EXISTS {name}" in SQL


def test_ec_db17_hnsw_params() -> None:
    """EC-DB17: HNSW m=16, ef_construction=64, cosine."""
    m = re.search(
        r"USING hnsw \(embedding_768 vector_cosine_ops\)\s+WITH \(m = 16, ef_construction = 64\)",
        SQL,
    )
    assert m is not None


def test_gin_indexes_use_expected_ops() -> None:
    assert "USING GIN (normalized_name gin_trgm_ops)" in SQL
    assert "USING GIN (search_tsv)" in SQL


def test_every_sandbox_namespace_not_null_default_live() -> None:
    cols = re.findall(r"^\s+sandbox_namespace\s+(.*?),?$", SQL, flags=re.M)
    assert cols, "protection_sessions phải có sandbox_namespace"
    for c in cols:
        assert "NOT NULL DEFAULT 'live'" in c


def test_ec_db15_rls_policy_and_force() -> None:
    """EC-DB15: RLS ENABLE + FORCE + pol_b2b_tenant_isolation."""
    assert "ALTER TABLE b2b_disbursement_shields ENABLE ROW LEVEL SECURITY" in SQL
    assert "ALTER TABLE b2b_disbursement_shields FORCE ROW LEVEL SECURITY" in SQL
    assert "CREATE POLICY pol_b2b_tenant_isolation ON b2b_disbursement_shields" in SQL
    assert "current_setting('app.current_partner_id', TRUE)" in SQL


def test_ec_db20_immutable_trigger() -> None:
    """EC-DB20: trigger chặn UPDATE/DELETE (và TRUNCATE) trên chuỗi băm."""
    assert "CREATE OR REPLACE FUNCTION forbid_audit_chain_mutation()" in SQL
    assert re.search(
        r"CREATE TRIGGER trg_immutable_audit_chain\s+BEFORE UPDATE OR DELETE ON tamper_evident_audit_chain",
        SQL,
    )
    assert "BEFORE TRUNCATE ON tamper_evident_audit_chain" in SQL


def test_audit_chain_not_granted_update_delete() -> None:
    block = SQL[SQL.index("DO $grants$") :]
    assert "GRANT SELECT, INSERT ON tamper_evident_audit_chain TO vayshield_app" in block
    assert not re.search(r"GRANT[^;]*(UPDATE|DELETE)[^;]*tamper_evident_audit_chain", block)
    # bảng audit không nằm trong danh sách GRANT đầy đủ quyền
    full = re.search(r"GRANT SELECT, INSERT, UPDATE, DELETE ON(.*?)TO vayshield_app", block, re.S)
    assert full is not None
    assert "tamper_evident_audit_chain" not in full.group(1)
    for t in TABLES:
        if t != "tamper_evident_audit_chain":
            assert t in full.group(1), t


def test_ec_db18_json_checks() -> None:
    """EC-DB18: CHECK kích thước < 64KB và độ sâu <= 5."""
    assert "octet_length(masked_extraction_json::text) < 65536" in SQL
    assert "jsonb_max_depth(masked_extraction_json) <= 5" in SQL


def test_ec_db26_control_char_check() -> None:
    """EC-DB26: CHECK chặn control char (trừ \\n \\t) ở lời khai."""
    assert "masked_user_utterance !~ '[\\x01-\\x08\\x0B-\\x1F]'" in SQL


def test_uuidv7_defined_and_used() -> None:
    assert "FUNCTION uuid_generate_v7()" in SQL
    assert "id                  UUID PRIMARY KEY DEFAULT uuid_generate_v7()" in SQL
    assert "gen_random_uuid() PRIMARY" not in SQL


def test_migration_is_idempotent_by_construction() -> None:
    assert not re.search(r"CREATE TABLE (?!IF NOT EXISTS)", SQL)
    assert not re.search(r"CREATE (UNIQUE )?INDEX (?!IF NOT EXISTS)", SQL)
    for trg in re.findall(r"CREATE TRIGGER (\w+)", SQL):
        assert f"DROP TRIGGER IF EXISTS {trg}" in SQL
    assert "DROP POLICY IF EXISTS pol_b2b_tenant_isolation" in SQL
    assert SQL.lstrip().startswith("--") and "BEGIN;" in SQL and SQL.rstrip().endswith("COMMIT;")


def test_foreign_keys_cascade_for_personal_data() -> None:
    assert re.search(
        r"borrower_device_id\s+UUID NOT NULL REFERENCES anonymous_devices\(id\) ON DELETE CASCADE",
        SQL,
    )
    assert re.search(
        r"device_id\s+UUID NOT NULL REFERENCES anonymous_devices\(id\) ON DELETE CASCADE", SQL
    )


def test_one_vote_per_device_per_mule() -> None:
    assert "UNIQUE (mule_node_id, reporter_device_id)" in SQL
