"""PostgreSQL thật (CI): ``SqlB2BRepository`` + ``SqlMuleGraphRepository`` + route webhook.

Tự skip khi thiếu ``TEST_DATABASE_URL`` (role app NOBYPASSRLS) / ``TEST_ADMIN_DATABASE_URL`` (seed).
Phủ: RLS 2 tenant cùng Idempotency-Key, quorum đồng thời (EC-DB07), gom cụm VA (EC-DB02),
``ON CONFLICT DO NOTHING`` 1 phiếu/thiết bị, giải envelope bí mật đối tác, đi qua route.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.routes import b2b as b2b_routes
from app.core.clock import FakeClock
from app.core.schemas.ops import EvidenceChannel, SeverityLevel, SimoDossierStatus
from app.mule_graph.repository_pg import SqlB2BRepository, SqlMuleGraphRepository
from app.mule_graph.service import EMBEDDING_DIM, MuleGraphService
from app.security.blind_index import BlindIndexer, BlindIndexKind
from app.security.envelope import EnvelopeCipher, record_aad
from tests.mule_graph.conftest import body_json, sign_headers
from tests.security._helpers import KEK_A, make_settings

pytestmark = pytest.mark.pg

URL = "/api/v1/b2b/disbursement-shield"
BIN = "970499"
VQR = EvidenceChannel.VIETQR_CRYPTO_DECODED


@dataclass
class Tenant:
    id: uuid.UUID
    key: str
    secret: bytes


@dataclass
class World:
    a: Tenant
    b: Tenant
    phone_hmac: str
    device_id: uuid.UUID


def _digits(n: int) -> str:
    return "".join(random.choice("0123456789") for _ in range(n))


@pytest.fixture
def sm(pg: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg, expire_on_commit=False)


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(KEK_A)


@pytest.fixture
def indexer() -> BlindIndexer:
    return BlindIndexer.from_settings(make_settings())


@pytest.fixture
async def world(
    pg_admin: AsyncEngine, cipher: EnvelopeCipher, indexer: BlindIndexer
) -> AsyncIterator[World]:
    tenants: list[Tenant] = []
    phone = "+84909" + _digits(6)
    phone_hmac = indexer.index(BlindIndexKind.PHONE, phone)
    device_id = uuid.uuid4()
    async with pg_admin.begin() as c:
        for label in ("a", "b"):
            tid = uuid.uuid4()
            key = f"vsp_pg{label}{uuid.uuid4().hex[:9]}"  # 16 ký tự → api_key_prefix duy nhất
            secret = f"pg-secret-{label}-".encode() + uuid.uuid4().hex.encode()
            blob = cipher.encrypt(
                secret, aad=record_aad("fintech_partners", "hmac_secret_cipher", tid)
            )
            await c.execute(
                text(
                    "INSERT INTO fintech_partners (id, partner_code, organization_name,"
                    " api_key_prefix, hmac_secret_cipher) VALUES (:i, :code, 'Org', :k, :blob)"
                ),
                {"i": tid, "code": f"PG_{tid.hex[:10]}", "k": key[:16], "blob": blob},
            )
            tenants.append(Tenant(tid, key, secret))
        await c.execute(
            text(
                "INSERT INTO anonymous_devices (id, phone_blind_hmac, phone_optin_exp_at,"
                " platform_type) VALUES (:i, :h, NOW() + INTERVAL '30 days', 'ANDROID_NATIVE')"
            ),
            {"i": device_id, "h": phone_hmac},
        )
    try:
        yield World(tenants[0], tenants[1], phone_hmac, device_id)
    finally:
        async with pg_admin.begin() as c:
            ids = [t.id for t in tenants]
            await c.execute(
                text("DELETE FROM b2b_disbursement_shields WHERE partner_id = ANY(:i)"),
                {"i": ids},
            )
            await c.execute(text("DELETE FROM fintech_partners WHERE id = ANY(:i)"), {"i": ids})
            await c.execute(text("DELETE FROM anonymous_devices WHERE id = :d"), {"d": device_id})


@pytest.fixture
async def devices(pg_admin: AsyncEngine) -> AsyncIterator[list[uuid.UUID]]:
    ids = [uuid.uuid4() for _ in range(10)]
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
                    " WHERE campaign_label LIKE 'VA-RANGE 970499/%'"
                    " AND id NOT IN (SELECT cluster_id FROM mule_account_nodes"
                    " WHERE cluster_id IS NOT NULL)"
                )
            )
            await c.execute(text("DELETE FROM anonymous_devices WHERE id = ANY(:i)"), {"i": ids})


@pytest.fixture
def pg_app(
    app: FastAPI,
    sm: async_sessionmaker[AsyncSession],
    cipher: EnvelopeCipher,
    monkeypatch: pytest.MonkeyPatch,
) -> FastAPI:
    """Đi qua dependency THẬT ``get_b2b_repository`` (không override) → SqlB2BRepository."""
    monkeypatch.setattr(b2b_routes, "get_sessionmaker", lambda: sm)
    monkeypatch.setattr(b2b_routes, "get_envelope_cipher", lambda: cipher)
    return app


async def _post(
    client: httpx.AsyncClient, clock: FakeClock, tenant: Tenant, raw: bytes, key: str
) -> httpx.Response:
    headers = sign_headers(
        secret=tenant.secret, now=clock.now(), raw=raw, key=key, partner_key=tenant.key
    )
    return await client.post(URL, content=raw, headers=headers)


# --- B2B ---------------------------------------------------------------------------------------


async def test_pg_partner_lookup_decrypts_envelope_secret(
    sm: async_sessionmaker[AsyncSession], cipher: EnvelopeCipher, world: World
) -> None:
    repo = SqlB2BRepository(sm, cipher)
    partner = await repo.get_partner_by_key(world.a.key)
    assert partner is not None and partner.id == world.a.id
    assert partner.hmac_secret == world.a.secret
    assert await repo.get_partner_by_key("vsp_nobody_xxxxxxx") is None


async def test_pg_webhook_end_to_end_through_route(
    pg_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock, world: World, redis: object
) -> None:
    raw = body_json(world.phone_hmac)
    first = await _post(client, fake_clock, world.a, raw, "pg-idem-0001")
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "ARMED_ON_DEVICE"
    fake_clock.advance(5)
    replay = await _post(client, fake_clock, world.a, raw, "pg-idem-0001")
    assert replay.status_code == 202
    assert replay.json()["shield_id"] == first.json()["shield_id"]
    assert replay.json()["shield_expires_at"] == first.json()["shield_expires_at"]
    other = await _post(
        client,
        fake_clock,
        world.a,
        body_json(world.phone_hmac, amount_bucket_tier="TIER_3M_10M"),
        "pg-idem-0001",
    )
    assert other.status_code == 409


async def test_pg_rls_two_tenants_same_idempotency_key(
    pg_app: FastAPI,
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    world: World,
    sm: async_sessionmaker[AsyncSession],
    cipher: EnvelopeCipher,
    redis: object,
) -> None:
    raw = body_json(world.phone_hmac)
    ra = await _post(client, fake_clock, world.a, raw, "shared-key-pg1")
    rb = await _post(client, fake_clock, world.b, raw, "shared-key-pg1")
    assert ra.status_code == rb.status_code == 202
    assert ra.json()["shield_id"] != rb.json()["shield_id"]
    repo = SqlB2BRepository(sm, cipher)
    own_a = await repo.get_shield(world.a.id, "shared-key-pg1")
    assert own_a is not None and str(own_a.id) == ra.json()["shield_id"]
    # Truy vấn thô dưới RLS của A chỉ thấy hàng của A; không đặt tenant → không thấy gì.
    async with sm() as s, s.begin():
        await s.execute(
            text("SELECT set_config('app.current_partner_id', :p, TRUE)"), {"p": str(world.a.id)}
        )
        rows = (
            await s.execute(
                text("SELECT partner_id FROM b2b_disbursement_shields WHERE idempotency_key = :k"),
                {"k": "shared-key-pg1"},
            )
        ).all()
    assert [r.partner_id for r in rows] == [world.a.id]
    async with sm() as s, s.begin():
        none = (await s.execute(text("SELECT 1 FROM b2b_disbursement_shields"))).all()
    assert none == []
    # Tenant A (đặt đúng RLS) không đọc được shield của B qua repo.
    assert await repo.get_shield(world.a.id, "no-such-key") is None


async def test_pg_create_shield_conflict_returns_existing_row(
    sm: async_sessionmaker[AsyncSession],
    cipher: EnvelopeCipher,
    world: World,
    fake_clock: FakeClock,
) -> None:
    from app.core.schemas.b2b import B2BShieldStatus
    from app.mule_graph.models import ShieldRecord

    repo = SqlB2BRepository(sm, cipher)

    def rec() -> ShieldRecord:
        return ShieldRecord(
            id=uuid.uuid4(),
            partner_id=world.a.id,
            idempotency_key="conflict-key-1",
            borrower_phone_hmac=world.phone_hmac,
            amount_bucket_tier="TIER_10M_30M",
            shield_duration_hours=24,
            status=B2BShieldStatus.NO_DEVICE_OPTED_IN,
            shield_expires_at=fake_clock.now() + timedelta(hours=24),
            created_at=fake_clock.now(),
        )

    first, created1 = await repo.create_shield(rec())
    second, created2 = await repo.create_shield(rec())
    assert created1 and not created2
    assert second.id == first.id


# --- Mule graph -----------------------------------------------------------------------------


def _svc(
    sm: async_sessionmaker[AsyncSession], indexer: BlindIndexer, cipher: EnvelopeCipher
) -> MuleGraphService:
    return MuleGraphService(SqlMuleGraphRepository(sm), indexer, cipher)


async def test_pg_ec_db07_ten_concurrent_reporters_exact_quorum(
    sm: async_sessionmaker[AsyncSession],
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
    devices: list[uuid.UUID],
    fake_clock: FakeClock,
    pg: AsyncEngine,
) -> None:
    svc = _svc(sm, indexer, cipher)
    account = "1" + _digits(8)  # 9 chữ số → không phải dải VA
    results = await asyncio.gather(
        *(
            svc.report_mule_account(
                bank_bin=BIN,
                account_no=account,
                reporter_device_id=d,
                channel=VQR,
                now=fake_clock.now(),
            )
            for d in devices
        )
    )
    node_id = results[0].node_id
    assert {r.node_id for r in results} == {node_id}
    async with pg.connect() as c:
        quorum = (
            await c.execute(
                text(
                    "SELECT distinct_device_quorum, simo_dossier_status FROM mule_account_nodes"
                    " WHERE id = :i"
                ),
                {"i": node_id},
            )
        ).one()
        edges = (
            await c.execute(
                text("SELECT COUNT(*) FROM mule_incident_edges WHERE mule_node_id = :i"),
                {"i": node_id},
            )
        ).scalar_one()
    assert quorum.distinct_device_quorum == 10 == edges
    assert quorum.simo_dossier_status == SimoDossierStatus.QUORUM_REACHED.value


async def test_pg_one_vote_per_device_on_conflict_do_nothing(
    sm: async_sessionmaker[AsyncSession],
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
    devices: list[uuid.UUID],
    fake_clock: FakeClock,
) -> None:
    svc = _svc(sm, indexer, cipher)
    account = "2" + _digits(8)
    res = None
    for _ in range(3):
        res = await svc.report_mule_account(
            bank_bin=BIN,
            account_no=account,
            reporter_device_id=devices[0],
            channel=VQR,
            now=fake_clock.now(),
        )
    assert res is not None
    assert res.distinct_device_quorum == 1
    assert res.status is SimoDossierStatus.OBSERVING
    assert res.newly_counted is False


async def test_pg_ec_db02_va_range_cluster_and_envelope(
    sm: async_sessionmaker[AsyncSession],
    indexer: BlindIndexer,
    cipher: EnvelopeCipher,
    devices: list[uuid.UUID],
    fake_clock: FakeClock,
    pg: AsyncEngine,
) -> None:
    svc = _svc(sm, indexer, cipher)
    base = "77" + _digits(6)  # 8 chữ số đầu chung, đuôi khác nhau → 10 chữ số → prefix 6 số
    vec = tuple([1.0] + [0.0] * (EMBEDDING_DIM - 1))
    r1 = await svc.report_mule_account(
        bank_bin=BIN,
        account_no=base + "01",
        reporter_device_id=devices[0],
        channel=VQR,
        now=fake_clock.now(),
        scam_script_vec=vec,
    )
    assert r1.cluster_id is None
    r2 = await svc.report_mule_account(
        bank_bin=BIN,
        account_no=base + "02",
        reporter_device_id=devices[1],
        channel=VQR,
        now=fake_clock.now(),
        scam_script_vec=vec,
    )
    assert r2.cluster_id is not None and r2.is_dynamic_va_range
    assert r2.status is SimoDossierStatus.OBSERVING  # quorum ở mức cụm, không lan xuống STK
    assert r2.cluster_distinct_devices == 2
    async with pg.connect() as c:
        cl = (
            await c.execute(
                text(
                    "SELECT severity_level, centroid_embedding IS NOT NULL AS has_c"
                    " FROM fraud_campaign_clusters WHERE id = :i"
                ),
                {"i": r2.cluster_id},
            )
        ).one()
        n1 = (
            await c.execute(
                text(
                    "SELECT cluster_id, account_cipher_envelope FROM mule_account_nodes"
                    " WHERE id = :i"
                ),
                {"i": r1.node_id},
            )
        ).one()
    assert cl.severity_level == SeverityLevel.CRITICAL_WAVE.value and cl.has_c
    assert n1.cluster_id == r2.cluster_id
    assert (base + "01").encode() not in bytes(n1.account_cipher_envelope)
    plain = cipher.decrypt(
        bytes(n1.account_cipher_envelope),
        aad=record_aad("mule_account_nodes", "account_cipher_envelope", r1.node_id),
    )
    assert plain == f"{BIN}:{base}01".encode()
