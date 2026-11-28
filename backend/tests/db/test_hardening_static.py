"""Test tĩnh migration 0003_hardening [T20] + thứ tự/checksum runner (không cần DB)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from app.db_schema.migrate import BOOTSTRAP_FILE, MIGRATIONS_DIR, checksum, list_migrations

SQL = (MIGRATIONS_DIR / "0003_hardening.sql").read_text(encoding="utf-8")


def test_runner_lists_0001_to_0003_in_order_without_bootstrap() -> None:
    names = [p.name for p in list_migrations()]
    assert names[:3] == ["0001_core_schema.sql", "0002_seed_support.sql", "0003_hardening.sql"]
    assert BOOTSTRAP_FILE not in names


def test_runner_checksum_is_sha256_of_bytes(tmp_path: Path) -> None:
    f = tmp_path / "0001_x.sql"
    f.write_bytes(b"SELECT 1;")
    assert checksum(f) == hashlib.sha256(b"SELECT 1;").hexdigest()
    assert [p.name for p in list_migrations(tmp_path)] == ["0001_x.sql"]
    f.write_bytes(b"SELECT 2;")
    assert checksum(f) != hashlib.sha256(b"SELECT 1;").hexdigest()


def test_0003_unique_pubkey_index_idempotent() -> None:
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_anonymous_devices_pubkey" in SQL
    assert "anonymous_devices(pubkey_ecdsa_p256)" in SQL
    assert "RAISE EXCEPTION" in SQL  # bản trùng cũ → báo rõ, không tự xóa dữ liệu
    assert not re.search(r"\bDELETE\s+FROM\b", SQL, re.IGNORECASE)


def test_0003_family_rls_forced_and_idempotent_policy() -> None:
    assert "ALTER TABLE family_guardian_links ENABLE ROW LEVEL SECURITY" in SQL
    assert "ALTER TABLE family_guardian_links FORCE ROW LEVEL SECURITY" in SQL
    drop = SQL.index("DROP POLICY IF EXISTS pol_family_owner_or_link ON family_guardian_links")
    assert drop < SQL.index("CREATE POLICY pol_family_owner_or_link ON family_guardian_links")
    assert "WITH CHECK" in SQL
    assert "app.current_device_id" in SQL and "app.current_guardian_link_id" in SQL


def test_0003_fk_set_null_each_drop_if_exists_before_add() -> None:
    for col, ref in [
        ("b2b_disbursement_shields_matched_session_id_fkey", "protection_sessions"),
        ("mule_incident_edges_source_audit_id_fkey", "contract_audits"),
        ("mule_incident_edges_source_intercept_id_fkey", "interception_events"),
    ]:
        assert f"DROP CONSTRAINT IF EXISTS {col}" in SQL
        assert re.search(
            rf"ADD CONSTRAINT {col}\s+FOREIGN KEY \(\w+\) REFERENCES {ref}\(id\)"
            r" ON DELETE SET NULL",
            SQL,
        )
    # reporter_device_id giữ nguyên (NOT NULL + quorum bia mộ EC-DB21)
    assert "reporter_device_id" in SQL and "ON DELETE SET NULL" in SQL
    assert "mule_incident_edges_reporter_device_id_fkey" not in SQL


def test_0001_and_0002_untouched_by_hardening() -> None:
    assert "uq_anonymous_devices_pubkey" not in (MIGRATIONS_DIR / "0001_core_schema.sql").read_text(
        encoding="utf-8"
    )


async def test_in_memory_repo_rejects_duplicate_pubkey_409() -> None:
    """Khóa công khai trùng → 409 (khớp hành vi ON CONFLICT của bản PG)."""
    import uuid

    import pytest

    from app.core.errors import AppError
    from app.services.audit_pipeline.repository import DeviceRecord, InMemoryDeviceRepository

    def dev() -> DeviceRecord:
        return DeviceRecord(
            device_id=uuid.uuid4(),
            pubkey_spki=b"\x30" * 91,
            platform_type="WEB_PWA",
            daily_wage_vnd=320_000,
            policy_version=1,
            phone_blind_hmac=None,
            pepper_version=1,
            phone_optin_exp_at=None,
        )

    repo = InMemoryDeviceRepository()
    await repo.create(dev())
    with pytest.raises(AppError) as exc:
        await repo.create(dev())
    assert exc.value.status_code == 409
