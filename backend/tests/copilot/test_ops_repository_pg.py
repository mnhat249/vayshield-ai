"""PostgreSQL thật (CI): ``SqlOpsRepository`` — cụm keyset, tên bên cho vay, hash-chain unseal."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from itertools import pairwise

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.copilot.deps import get_ops_cipher, get_ops_repository
from app.copilot.ops_auth import get_ops_token_service
from app.copilot.repository import ClusterCursor, OpsRepository
from app.copilot.repository_pg import SqlOpsRepository
from app.core.clock import FakeClock
from app.core.schemas.ops import EvidenceChannel, ScamTrap, SeverityLevel
from app.mule_graph.repository_pg import SqlMuleGraphRepository
from app.mule_graph.service import MuleGraphService
from app.security.blind_index import BlindIndexer
from app.security.envelope import EnvelopeCipher
from tests.security._helpers import KEK_A, make_settings

from .conftest import vec

pytestmark = pytest.mark.pg

BIN = "970498"


def _digits(n: int) -> str:
    return "".join(random.choice("0123456789") for _ in range(n))


@pytest.fixture
def sm(pg: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg, expire_on_commit=False)


@pytest.fixture
def pg_cipher() -> EnvelopeCipher:
    return EnvelopeCipher(KEK_A)


@pytest.fixture
def sql_repo(sm: async_sessionmaker[AsyncSession]) -> SqlOpsRepository:
    return SqlOpsRepository(sm, SqlMuleGraphRepository(sm))


@pytest.fixture
async def pg_devices(pg_admin: AsyncEngine) -> AsyncIterator[list[uuid.UUID]]:
    ids = [uuid.uuid4() for _ in range(3)]
    async with pg_admin.begin() as c:
        for i in ids:
            await c.execute(
                text("INSERT INTO anonymous_devices (id, platform_type) VALUES (:i, 'WEB_PWA')"),
                {"i": i},
            )
    try:
        yield ids
    finally:
        async with pg_admin.begin() as c:
            await c.execute(
                text(
                    "DELETE FROM mule_account_nodes WHERE id IN (SELECT mule_node_id FROM"
                    " mule_incident_edges WHERE reporter_device_id = ANY(:i))"
                ),
                {"i": ids},
            )
            await c.execute(
                text(
                    "DELETE FROM fraud_campaign_clusters"
                    " WHERE campaign_label LIKE 'VA-RANGE 970498/%'"
                    " AND id NOT IN (SELECT cluster_id FROM mule_account_nodes"
                    " WHERE cluster_id IS NOT NULL)"
                )
            )
            await c.execute(text("DELETE FROM anonymous_devices WHERE id = ANY(:i)"), {"i": ids})


async def _seed(
    sql_repo: SqlOpsRepository,
    cipher: EnvelopeCipher,
    devices: list[uuid.UUID],
    now: object,
) -> tuple[str, uuid.UUID]:
    """2 STK cùng dải VA (8 số đầu) -> 1 cụm; trả (STK thô của nút đầu, node_id)."""
    prefix = _digits(8)
    acct_a, acct_b = prefix + _digits(8), prefix + _digits(8)
    svc = MuleGraphService(sql_repo.graph, BlindIndexer.from_settings(make_settings()), cipher)
    first = None
    for acct, dev in ((acct_a, devices[0]), (acct_a, devices[1]), (acct_b, devices[2])):
        res = await svc.report_mule_account(
            bank_bin=BIN,
            account_no=acct,
            reporter_device_id=dev,
            channel=EvidenceChannel.VIETQR_CRYPTO_DECODED,
            now=now,  # type: ignore[arg-type]
            scam_script_vec=vec(0),
            scam_trap=ScamTrap.TRAP_1_ADVANCE_FEE,
        )
        first = first or res
    assert first is not None
    return acct_a, first.node_id


async def test_pg_list_clusters_keyset_and_filters(
    sql_repo: SqlOpsRepository,
    pg_cipher: EnvelopeCipher,
    pg_devices: list[uuid.UUID],
    fake_clock: FakeClock,
) -> None:
    await _seed(sql_repo, pg_cipher, pg_devices, fake_clock.now())
    rows = await sql_repo.list_clusters(severity=None, limit=500, after=None)
    mine = [c for c in rows if c.campaign_label.startswith(f"VA-RANGE {BIN}/")]
    assert len(mine) == 1
    assert mine[0].severity_level in set(SeverityLevel)
    # Thứ tự (last_seen_at DESC, id DESC) + keyset: trang sau không lặp trang trước.
    keys = [(c.last_seen_at, c.id) for c in rows]
    assert keys == sorted(keys, reverse=True)
    if len(rows) >= 2:
        nxt = await sql_repo.list_clusters(
            severity=None, limit=500, after=ClusterCursor(rows[0].last_seen_at, rows[0].id)
        )
        assert rows[0].id not in {c.id for c in nxt}
    only = await sql_repo.list_clusters(severity=SeverityLevel.CRITICAL_WAVE, limit=500, after=None)
    assert all(c.severity_level is SeverityLevel.CRITICAL_WAVE for c in only)


async def test_pg_lender_names(sql_repo: SqlOpsRepository, pg_admin: AsyncEngine) -> None:
    lid = uuid.uuid4()
    code = f"PGTEST_{uuid.uuid4().hex[:10]}"
    async with pg_admin.begin() as c:
        await c.execute(
            text(
                "INSERT INTO licensed_lender_registry (id, entity_code, legal_category,"
                " official_short_name, official_legal_name, normalized_name)"
                " VALUES (:i, :c, 'LICENSED_FINCO', 'Bên Test', 'Bên Test Co', 'ben test')"
            ),
            {"i": lid, "c": code},
        )
    try:
        assert await sql_repo.lender_names([lid, uuid.uuid4()]) == {lid: "Bên Test"}
        assert await sql_repo.lender_names([]) == {}
    finally:
        async with pg_admin.begin() as c:
            await c.execute(text("DELETE FROM licensed_lender_registry WHERE id = :i"), {"i": lid})


async def test_pg_concurrent_unseal_audit_chain_does_not_fork(
    sql_repo: SqlOpsRepository, pg: AsyncEngine, fake_clock: FakeClock
) -> None:
    """8 unseal đồng thời: mỗi mục nối đúng vào mục trước, chuỗi toàn vẹn, không phân nhánh."""
    targets = [uuid.uuid4() for _ in range(8)]
    hashes = await asyncio.gather(
        *(
            sql_repo.append_audit(
                "analyst@example.test", "SIMO_DOSSIER_UNSEAL", t, fake_clock.now()
            )
            for t in targets
        )
    )
    assert len(set(hashes)) == 8
    async with pg.connect() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT seq_no, prev_entry_hash, entry_hash FROM tamper_evident_audit_chain"
                    " WHERE entry_hash = ANY(:h) ORDER BY seq_no"
                ),
                {"h": list(hashes)},
            )
        ).all()
        assert len(rows) == 8
        # Mỗi prev_entry_hash là entry_hash của mục ngay trước theo seq_no (không nhánh).
        all_rows = (
            await c.execute(
                text(
                    "SELECT seq_no, prev_entry_hash, entry_hash FROM tamper_evident_audit_chain"
                    " WHERE seq_no >= :lo ORDER BY seq_no"
                ),
                {"lo": rows[0].seq_no},
            )
        ).all()
        for prev, cur in pairwise(all_rows):
            assert cur.prev_entry_hash == prev.entry_hash
        assert (await c.execute(text("SELECT verify_audit_chain()"))).scalar_one() is None


async def test_pg_dossier_route_no_raw_account_and_unseal_audited(
    app: FastAPI,
    sql_repo: SqlOpsRepository,
    pg_cipher: EnvelopeCipher,
    pg_devices: list[uuid.UUID],
    fake_clock: FakeClock,
    client: httpx.AsyncClient,
    pg: AsyncEngine,
) -> None:
    from app.copilot.ops_auth import (
        ROLE_FRAUD_ANALYST,
        InMemoryOpsUserDirectory,
        OpsPrincipal,
        OpsTokenService,
    )

    from .conftest import make_token_service

    acct, node_id = await _seed(
        sql_repo, pg_cipher, pg_devices, fake_clock.now() - timedelta(minutes=1)
    )
    tokens: OpsTokenService = make_token_service(
        fake_clock, InMemoryOpsUserDirectory(iterations=1000)
    )
    repo: OpsRepository = sql_repo
    app.dependency_overrides[get_ops_token_service] = lambda: tokens
    app.dependency_overrides[get_ops_repository] = lambda: repo
    app.dependency_overrides[get_ops_cipher] = lambda: pg_cipher
    hdr = {
        "Authorization": "Bearer "
        + tokens.issue(OpsPrincipal("analyst@example.test", (ROLE_FRAUD_ANALYST,))).access_token
    }
    plain = await client.get(
        "/api/v1/ops/simo-dossier", params={"mule_node_id": str(node_id)}, headers=hdr
    )
    assert plain.status_code == 200
    assert acct not in plain.text and acct[:8] not in plain.text
    assert not any(str(d) in plain.text for d in pg_devices)
    opened = await client.get(
        "/api/v1/ops/simo-dossier",
        params={"mule_node_id": str(node_id), "unseal": "true"},
        headers=hdr,
    )
    assert opened.status_code == 200
    body = opened.json()
    assert body["unsealed_account_number"] == acct
    async with pg.connect() as c:
        found = (
            await c.execute(
                text(
                    "SELECT count(*) FROM tamper_evident_audit_chain"
                    " WHERE entry_hash = :h AND target_ref_id = :t"
                ),
                {"h": body["audit_chain_entry_hash"], "t": node_id},
            )
        ).scalar_one()
    assert found == 1
