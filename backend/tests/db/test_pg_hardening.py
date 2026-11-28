"""PostgreSQL thật (CI) cho migration 0003_hardening [T20]; tự skip nếu thiếu TEST_DATABASE_URL."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core import db as core_db
from app.core.errors import AppError
from app.db_schema.migrate import run_migrations
from app.services.audit_pipeline.repository import DeviceRecord, SqlDeviceRepository

pytestmark = pytest.mark.pg


@pytest.fixture
async def bound_db(pg: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    core_db._engine = pg
    core_db._sessionmaker = async_sessionmaker(pg, expire_on_commit=False)
    try:
        yield pg
    finally:
        core_db._engine = None
        core_db._sessionmaker = None


def _device(pubkey: bytes) -> DeviceRecord:
    return DeviceRecord(
        device_id=uuid.uuid4(),
        pubkey_spki=pubkey,
        platform_type="ANDROID_NATIVE",
        daily_wage_vnd=350_000,
        policy_version=1,
        phone_blind_hmac=None,
        pepper_version=1,
        phone_optin_exp_at=None,
    )


async def test_migrations_apply_0001_to_0003_in_order_and_idempotent(
    pg_admin: AsyncEngine,
) -> None:
    url = pg_admin.url.render_as_string(hide_password=False)
    await run_migrations(url)
    assert await run_migrations(url) == []
    async with pg_admin.connect() as c:
        names = [
            r[0]
            for r in await c.execute(
                text("SELECT filename FROM schema_migrations ORDER BY filename")
            )
        ]
    assert names[:3] == ["0001_core_schema.sql", "0002_seed_support.sql", "0003_hardening.sql"]


async def test_unique_pubkey_rejects_duplicate_at_db_level(pg_admin: AsyncEngine) -> None:
    pk = b"\x30" + uuid.uuid4().bytes * 5
    async with pg_admin.begin() as c:
        await c.execute(
            text(
                "INSERT INTO anonymous_devices (pubkey_ecdsa_p256, platform_type)"
                " VALUES (:p, 'WEB_PWA')"
            ),
            {"p": pk},
        )
    try:
        with pytest.raises(IntegrityError, match="uq_anonymous_devices_pubkey"):
            async with pg_admin.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO anonymous_devices (pubkey_ecdsa_p256, platform_type)"
                        " VALUES (:p, 'WEB_PWA')"
                    ),
                    {"p": pk},
                )
        async with pg_admin.begin() as c:  # NULL (bia mộ) vẫn được nhiều dòng
            for _ in range(2):
                await c.execute(
                    text("INSERT INTO anonymous_devices (platform_type) VALUES ('WEB_PWA')")
                )
    finally:
        async with pg_admin.begin() as c:
            await c.execute(
                text("DELETE FROM anonymous_devices WHERE pubkey_ecdsa_p256 = :p"), {"p": pk}
            )


async def test_register_race_same_pubkey_one_wins_other_409(bound_db: AsyncEngine) -> None:
    """Đăng ký đồng thời cùng khóa: đúng 1 thành công, còn lại AppError 409 (không 500)."""
    repo = SqlDeviceRepository()
    pk = b"\x30" + uuid.uuid4().bytes * 5
    results = await asyncio.gather(
        *(repo.create(_device(pk)) for _ in range(4)), return_exceptions=True
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    assert len(errors) == 3
    assert all(isinstance(e, AppError) and e.status_code == 409 for e in errors)
    assert await repo.pubkey_exists(pk)


@pytest.fixture
async def two_families(pg_admin: AsyncEngine) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    dev_a, dev_b = uuid.uuid4(), uuid.uuid4()
    async with pg_admin.begin() as c:
        for d in (dev_a, dev_b):
            await c.execute(
                text("INSERT INTO anonymous_devices (id, platform_type) VALUES (:d, 'WEB_PWA')"),
                {"d": d},
            )
            await c.execute(
                text(
                    "INSERT INTO family_guardian_links (borrower_device_id, guardian_alias,"
                    " status) VALUES (:d, 'Người thân', 'ACTIVE')"
                ),
                {"d": d},
            )
    yield dev_a, dev_b
    async with pg_admin.begin() as c:
        await c.execute(
            text("DELETE FROM anonymous_devices WHERE id IN (:a, :b)"), {"a": dev_a, "b": dev_b}
        )


async def test_family_links_rls_isolates_two_users(
    pg: AsyncEngine, two_families: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_families
    async with pg.begin() as c:  # không ngữ cảnh → không thấy gì
        assert (
            await c.execute(text("SELECT count(*) FROM family_guardian_links"))
        ).scalar_one() == 0
    async with pg.begin() as c:
        await c.execute(text("SELECT set_config('app.current_device_id', :d, true)"), {"d": str(a)})
        rows = (await c.execute(text("SELECT borrower_device_id FROM family_guardian_links"))).all()
        assert [r[0] for r in rows] == [a]
        # không UPDATE/DELETE được liên kết của người khác
        upd = await c.execute(
            text(
                "UPDATE family_guardian_links SET status = 'REVOKED_BY_BORROWER'"
                " WHERE borrower_device_id = :b"
            ),
            {"b": b},
        )
        assert upd.rowcount == 0
    # người thân theo link id chỉ thấy đúng 1 link
    async with pg.begin() as c:
        await c.execute(text("SELECT set_config('app.current_device_id', :d, true)"), {"d": str(b)})
        link_b = (await c.execute(text("SELECT id FROM family_guardian_links"))).scalar_one()
    async with pg.begin() as c:
        await c.execute(
            text("SELECT set_config('app.current_guardian_link_id', :l, true)"), {"l": str(link_b)}
        )
        rows = (await c.execute(text("SELECT id FROM family_guardian_links"))).all()
        assert [r[0] for r in rows] == [link_b]


async def test_family_links_rls_blocks_insert_for_other_device(
    pg: AsyncEngine, two_families: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_families
    with pytest.raises(DBAPIError, match="row-level security"):
        async with pg.begin() as c:
            await c.execute(
                text("SELECT set_config('app.current_device_id', :d, true)"), {"d": str(a)}
            )
            await c.execute(
                text(
                    "INSERT INTO family_guardian_links (borrower_device_id, guardian_alias,"
                    " status) VALUES (:b, 'x', 'PENDING_CONFIRM')"
                ),
                {"b": b},
            )


async def test_family_links_rls_enabled_forced_in_catalog(pg_admin: AsyncEngine) -> None:
    async with pg_admin.connect() as c:
        row = (
            await c.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE relname = 'family_guardian_links'"
                )
            )
        ).one()
    assert tuple(row) == (True, True)


async def test_delete_device_with_b2b_and_mule_refs_no_fk_error(
    pg_admin: AsyncEngine,
) -> None:
    """Xóa thẳng phiên/audit/intercept (kể cả bằng admin, không qua erasure.py) không lỗi FK:
    các cột tham chiếu được SET NULL."""
    dev, sess, mule, partner = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    edge = uuid.uuid4()
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
        await c.execute(
            text(
                "INSERT INTO fintech_partners (id, partner_code, organization_name,"
                " api_key_prefix, hmac_secret_cipher) VALUES (:i, :code, 'Org', :pre, '\\x00')"
            ),
            {"i": partner, "code": f"P_{partner.hex[:8]}", "pre": partner.hex[:12]},
        )
        await c.execute(
            text(
                "INSERT INTO b2b_disbursement_shields (partner_id, idempotency_key,"
                " borrower_phone_hmac, matched_session_id, amount_bucket_tier, status)"
                " VALUES (:p, 'k-t20', 'h', :s, 'TIER_10M_30M', 'ARMED_ON_DEVICE')"
            ),
            {"p": partner, "s": sess},
        )
        await c.execute(
            text(
                "INSERT INTO mule_account_nodes (id, bank_bin, account_hmac_sha256,"
                " masked_account_display, account_cipher_envelope)"
                " VALUES (:m, '970436', :h, '****6789', '\\x00')"
            ),
            {"m": mule, "h": uuid.uuid4().hex * 2},
        )
        await c.execute(
            text(
                "INSERT INTO mule_incident_edges (id, mule_node_id, reporter_device_id,"
                " evidence_channel) VALUES (:e, :m, :d, 'VIETQR_CRYPTO_DECODED')"
            ),
            {"e": edge, "m": mule, "d": dev},
        )
    try:
        async with pg_admin.begin() as c:
            await c.execute(text("DELETE FROM protection_sessions WHERE id = :s"), {"s": sess})
        async with pg_admin.connect() as c:
            matched = (
                await c.execute(
                    text(
                        "SELECT matched_session_id FROM b2b_disbursement_shields"
                        " WHERE partner_id = :p"
                    ),
                    {"p": partner},
                )
            ).scalar_one()
        assert matched is None
    finally:
        async with pg_admin.begin() as c:
            await c.execute(text("DELETE FROM mule_incident_edges WHERE id = :e"), {"e": edge})
            await c.execute(
                text("DELETE FROM b2b_disbursement_shields WHERE partner_id = :p"), {"p": partner}
            )
            await c.execute(text("DELETE FROM fintech_partners WHERE id = :p"), {"p": partner})
            await c.execute(text("DELETE FROM mule_account_nodes WHERE id = :m"), {"m": mule})
            await c.execute(text("DELETE FROM anonymous_devices WHERE id = :d"), {"d": dev})


async def test_fk_delete_rules_in_catalog(pg_admin: AsyncEngine) -> None:
    async with pg_admin.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT conname, confdeltype::text FROM pg_constraint WHERE conname IN ("
                    "'b2b_disbursement_shields_matched_session_id_fkey',"
                    "'mule_incident_edges_source_audit_id_fkey',"
                    "'mule_incident_edges_source_intercept_id_fkey')"
                )
            )
        ).all()
    assert len(rows) == 3 and {r[1] for r in rows} == {"n"}  # n = SET NULL
