# ruff: noqa: E501
"""Test PostgreSQL thật (CI): RLS 2 tenant, trigger audit chain, JSON/null byte, migration 2 lần.

Dùng ``pg`` (role app, NOBYPASSRLS) cho kiểm tra RLS; ``pg_admin`` chỉ để seed/DDL.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from itertools import pairwise

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db_schema.migrate import run_migrations

pytestmark = pytest.mark.pg


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
async def two_tenants(pg_admin: AsyncEngine) -> AsyncIterator[tuple[str, str]]:
    a, b = _uid(), _uid()
    async with pg_admin.begin() as c:
        for pid in (a, b):
            await c.execute(
                text(
                    "INSERT INTO fintech_partners (id, partner_code, organization_name,"
                    " api_key_prefix, hmac_secret_cipher) VALUES (:i, :code, 'Org', :pre, '\\x00')"
                ),
                {"i": pid, "code": f"P_{pid[:8]}", "pre": pid[:12]},
            )
            await c.execute(
                text(
                    "INSERT INTO b2b_disbursement_shields (id, partner_id, idempotency_key,"
                    " borrower_phone_hmac, amount_bucket_tier, status)"
                    " VALUES (:i, :p, :k, 'h', 'TIER_10M_30M', 'ARMED_ON_DEVICE')"
                ),
                {"i": _uid(), "p": pid, "k": f"k-{pid[:8]}"},
            )
    yield a, b
    async with pg_admin.begin() as c:
        await c.execute(
            text("DELETE FROM b2b_disbursement_shields WHERE partner_id IN (:a, :b)"),
            {"a": a, "b": b},
        )
        await c.execute(text("DELETE FROM fintech_partners WHERE id IN (:a, :b)"), {"a": a, "b": b})


async def test_ec_db15_rls_blocks_cross_tenant(
    pg: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """EC-DB15: SELECT * không WHERE chỉ thấy dòng của tenant hiện tại."""
    a, b = two_tenants
    async with pg.begin() as c:
        await c.execute(text("SELECT set_config('app.current_partner_id', :p, TRUE)"), {"p": a})
        rows = (
            await c.execute(text("SELECT partner_id::text FROM b2b_disbursement_shields"))
        ).all()
        assert {r[0] for r in rows} == {a}
    async with pg.begin() as c:
        await c.execute(text("SELECT set_config('app.current_partner_id', :p, TRUE)"), {"p": b})
        rows = (
            await c.execute(text("SELECT partner_id::text FROM b2b_disbursement_shields"))
        ).all()
        assert {r[0] for r in rows} == {b}


async def test_ec_db15_no_tenant_context_sees_nothing_and_cannot_insert(
    pg: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    a, b = two_tenants
    async with pg.begin() as c:
        rows = (await c.execute(text("SELECT 1 FROM b2b_disbursement_shields"))).all()
        assert rows == []
    with pytest.raises(DBAPIError):
        async with pg.begin() as c:
            await c.execute(text("SELECT set_config('app.current_partner_id', :p, TRUE)"), {"p": a})
            await c.execute(
                text(
                    "INSERT INTO b2b_disbursement_shields (id, partner_id, idempotency_key,"
                    " borrower_phone_hmac, amount_bucket_tier, status)"
                    " VALUES (:i, :p, 'x', 'h', 'T', 'ARMED_ON_DEVICE')"
                ),
                {"i": _uid(), "p": b},
            )


async def test_ec_db15_force_rls_enabled(pg_admin: AsyncEngine) -> None:
    async with pg_admin.connect() as c:
        row = (
            await c.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE relname = 'b2b_disbursement_shields'"
                )
            )
        ).one()
    assert tuple(row) == (True, True)


async def test_ec_db20_update_delete_audit_chain_blocked(pg: AsyncEngine) -> None:
    """EC-DB20: UPDATE/DELETE bị chặn (cả bởi GRANT lẫn trigger)."""
    async with pg.begin() as c:
        seq = (
            await c.execute(text("SELECT append_audit_chain('ops', 'TEST', :t)"), {"t": _uid()})
        ).scalar_one()
    for stmt in (
        "UPDATE tamper_evident_audit_chain SET actor_id = 'x' WHERE seq_no = :s",
        "DELETE FROM tamper_evident_audit_chain WHERE seq_no = :s",
    ):
        with pytest.raises(DBAPIError):
            async with pg.begin() as c:
                await c.execute(text(stmt), {"s": seq})


async def test_ec_db20_trigger_blocks_even_admin(pg_admin: AsyncEngine) -> None:
    async with pg_admin.begin() as c:
        seq = (
            await c.execute(text("SELECT append_audit_chain('ops', 'TEST', :t)"), {"t": _uid()})
        ).scalar_one()
    for stmt in (
        "UPDATE tamper_evident_audit_chain SET actor_id = 'x' WHERE seq_no = :s",
        "DELETE FROM tamper_evident_audit_chain WHERE seq_no = :s",
    ):
        with pytest.raises(DBAPIError, match="IMMUTABLE_AUDIT_CHAIN_VIOLATION"):
            async with pg_admin.begin() as c:
                await c.execute(text(stmt), {"s": seq})
    with pytest.raises(DBAPIError, match="IMMUTABLE_AUDIT_CHAIN_VIOLATION"):
        async with pg_admin.begin() as c:
            await c.execute(text("TRUNCATE tamper_evident_audit_chain"))


async def test_hash_chain_continuous_in_db(pg: AsyncEngine) -> None:
    async with pg.begin() as c:
        for i in range(3):
            await c.execute(
                text("SELECT append_audit_chain('ops', 'CHAIN_TEST', :t)"),
                {"t": str(uuid.UUID(int=i))},
            )
    async with pg.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT seq_no, prev_entry_hash, entry_hash FROM tamper_evident_audit_chain ORDER BY seq_no"
                )
            )
        ).all()
        assert rows[0][1] == "0" * 64
        for prev, cur in pairwise(rows):
            assert cur[1] == prev[2]
        assert (await c.execute(text("SELECT verify_audit_chain()"))).scalar_one() is None


async def test_chain_rejects_forged_insert(pg: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="AUDIT_CHAIN_BROKEN_LINK"):
        async with pg.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO tamper_evident_audit_chain"
                    " (actor_id, action_type, target_ref_id, prev_entry_hash, entry_hash)"
                    " VALUES ('a', 'b', :t, :p, :h)"
                ),
                {"t": _uid(), "p": "f" * 64, "h": "e" * 64},
            )


@pytest.fixture
async def session_id(pg_admin: AsyncEngine) -> AsyncIterator[str]:
    dev, sess = _uid(), _uid()
    async with pg_admin.begin() as c:
        await c.execute(
            text("INSERT INTO anonymous_devices (id, platform_type) VALUES (:d, 'WEB_PWA')"),
            {"d": dev},
        )
        await c.execute(
            text(
                "INSERT INTO protection_sessions (id, device_id, current_hsm_state, trigger_origin)"
                " VALUES (:s, :d, 'S0_IDLE', 'SANDBOX_SIM')"
            ),
            {"s": sess, "d": dev},
        )
    yield sess
    async with pg_admin.begin() as c:
        await c.execute(text("DELETE FROM anonymous_devices WHERE id = :d"), {"d": dev})


_AUDIT_INSERT = (
    "INSERT INTO contract_audits (id, session_id, image_count, verdict_code, lender_category_resolved,"
    " masked_extraction_json, processing_latency_ms, model_tier_used)"
    " VALUES (:i, :s, 1, 'V_GREEN_TRANSPARENT', 'X', CAST(:j AS jsonb), 10, 'GEMINI_FLASH')"
)


async def test_sandbox_namespace_defaults_live(pg: AsyncEngine, session_id: str) -> None:
    async with pg.connect() as c:
        ns = (
            await c.execute(
                text("SELECT sandbox_namespace FROM protection_sessions WHERE id = :s"),
                {"s": session_id},
            )
        ).scalar_one()
    assert ns == "live"


async def test_ec_db18_json_too_deep_rejected(pg: AsyncEngine, session_id: str) -> None:
    deep: object = 1
    for _ in range(50):
        deep = {"x": deep}
    with pytest.raises(DBAPIError, match="ck_masked_extraction_depth"):
        async with pg.begin() as c:
            await c.execute(
                text(_AUDIT_INSERT), {"i": _uid(), "s": session_id, "j": json.dumps(deep)}
            )


async def test_ec_db18_70kb_accepted_by_sql_pydantic_is_gate(
    pg: AsyncEngine, session_id: str
) -> None:
    """70KB < 128KB: CHECK SQL (lưới an toàn) chấp nhận; chốt chặn 64KB là Pydantic."""
    async with pg.begin() as c:
        await c.execute(
            text(_AUDIT_INSERT),
            {"i": _uid(), "s": session_id, "j": json.dumps({"b": "x" * 70_000})},
        )


async def test_ec_db18_json_too_heavy_rejected(pg: AsyncEngine, session_id: str) -> None:
    with pytest.raises(DBAPIError, match="ck_masked_extraction_size"):
        async with pg.begin() as c:
            await c.execute(
                text(_AUDIT_INSERT),
                {"i": _uid(), "s": session_id, "j": json.dumps({"b": "x" * 140_000})},
            )


async def test_ec_db18_json_ok_accepted(pg: AsyncEngine, session_id: str) -> None:
    async with pg.begin() as c:
        await c.execute(
            text(_AUDIT_INSERT),
            {"i": _uid(), "s": session_id, "j": json.dumps({"a": {"b": [1, 2]}})},
        )


async def test_ec_db26_null_byte_rejected_and_control_char_check(
    pg: AsyncEngine, session_id: str
) -> None:
    """EC-DB26: PG từ chối \\u0000; CHECK chặn control char khác."""
    with pytest.raises((DBAPIError, ProgrammingError)):
        async with pg.begin() as c:
            await c.execute(
                text(_AUDIT_INSERT), {"i": _uid(), "s": session_id, "j": '{"t": "a\\u0000b"}'}
            )
    with pytest.raises(DBAPIError, match="masked_user_utterance"):
        async with pg.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO interception_events (id, session_id, audio_call_state, computed_scam_prob,"
                    " masked_user_utterance, outcome_action) VALUES (:i, :s, 'NONE', 0.5, :u, 'USER_ABORTED_TRANSFER')"
                ),
                {"i": _uid(), "s": session_id, "u": "abc\x07def"},
            )


async def test_ec_db17_hnsw_params_in_catalog(pg_admin: AsyncEngine) -> None:
    async with pg_admin.connect() as c:
        defn = (
            await c.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_legal_hnsw_vec'")
            )
        ).scalar_one()
    assert (
        "hnsw" in defn
        and "m='16'" in defn.replace(" ", "")
        and "ef_construction='64'" in defn.replace(" ", "")
    )


async def test_uuid_v7_function_version_and_order(pg: AsyncEngine) -> None:
    async with pg.connect() as c:
        ids = [
            (await c.execute(text("SELECT uuid_generate_v7()::text"))).scalar_one()
            for _ in range(3)
        ]
    assert all(i[14] == "7" for i in ids)
    assert [i[:13] for i in ids] == sorted(i[:13] for i in ids)  # 48-bit ms tăng dần


async def test_migration_runs_twice_without_error(pg_admin: AsyncEngine) -> None:
    """DoD: chạy 2 lần không lỗi (runner + SQL idempotent)."""
    url = pg_admin.url.render_as_string(hide_password=False)
    await run_migrations(url)
    again = await run_migrations(url)
    assert again == []
    # chạy lại thẳng file SQL (bỏ qua bảng theo dõi) cũng không lỗi
    from app.db_schema.migrate import list_migrations

    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        await conn.execute(list_migrations()[0].read_text(encoding="utf-8"))
    finally:
        await conn.close()


async def test_audit_chain_rejects_pipe_in_fields(pg: AsyncEngine) -> None:
    with pytest.raises(DBAPIError, match="ck_audit_no_pipe"):
        async with pg.begin() as c:
            await c.execute(text("SELECT append_audit_chain('a|b', 'X', :t)"), {"t": _uid()})


async def test_audit_chain_rejects_non_read_committed(pg: AsyncEngine) -> None:
    for level in ("REPEATABLE READ", "SERIALIZABLE"):
        with pytest.raises(DBAPIError, match="AUDIT_CHAIN_ISOLATION"):
            async with pg.connect() as c:
                await c.execution_options(isolation_level=level)
                await c.execute(text("SELECT append_audit_chain('ops', 'ISO', :t)"), {"t": _uid()})


async def test_audit_chain_concurrent_append_no_fork(pg: AsyncEngine) -> None:
    async def one() -> None:
        async with pg.begin() as c:
            await c.execute(text("SELECT append_audit_chain('ops', 'CONC', :t)"), {"t": _uid()})

    await asyncio.gather(*(one() for _ in range(10)))
    async with pg.connect() as c:
        dup = (
            await c.execute(
                text(
                    "SELECT count(*) FROM (SELECT prev_entry_hash FROM tamper_evident_audit_chain"
                    " GROUP BY 1 HAVING count(*) > 1) d"
                )
            )
        ).scalar_one()
        assert dup == 0
        assert (await c.execute(text("SELECT verify_audit_chain()"))).scalar_one() is None


async def test_every_uuid_pk_has_v7_default(pg_admin: AsyncEngine) -> None:
    async with pg_admin.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT table_name FROM information_schema.columns WHERE table_schema = 'public'"
                    " AND column_name = 'id' AND data_type = 'uuid'"
                    " AND (column_default IS NULL OR column_default NOT LIKE '%uuid_generate_v7%')"
                )
            )
        ).all()
    assert rows == []
