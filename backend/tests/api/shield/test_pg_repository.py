"""PostgreSQL thật (CI): kho liên kết người thân + lưu/đọc phiên bảo vệ (EC-DB11, EC-DB14)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core import db as core_db
from app.core.schemas.common import AmountBucketTier, HsmState, TriggerOrigin
from app.core.schemas.family import FallbackContact
from app.security.envelope import EnvelopeCipher
from app.services.realtime.models import LinkStatus
from app.services.realtime.persistence import PgFallbackReader, PgShieldPersistence
from app.services.realtime.repository import SqlFamilyRepository
from app.shield_state.fibers import EvidenceSource, LoanJourneyFiber

from ._flows import PUSH_SUB


@pytest.fixture
async def bound_db(pg: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    core_db._engine = pg
    core_db._sessionmaker = async_sessionmaker(pg, expire_on_commit=False)
    try:
        yield pg
    finally:
        core_db._engine = None
        core_db._sessionmaker = None


@pytest.mark.pg
async def test_ec_db14_sql_family_repo_roundtrip_and_push_expired(
    bound_db: AsyncEngine, pg_admin: AsyncEngine
) -> None:
    device, link_id = uuid.uuid4(), uuid.uuid4()
    async with pg_admin.begin() as c:
        await c.execute(
            text("INSERT INTO anonymous_devices (id, platform_type) VALUES (:d, 'WEB_PWA')"),
            {"d": device},
        )
    repo = SqlFamilyRepository(EnvelopeCipher(bytes(range(32))))
    now = datetime.now(UTC)
    await repo.create_pending(
        link_id=link_id,
        device_id=device,
        guardian_alias="Con trai",
        borrower_display_label="Mẹ",
        pairing_code_hash="a" * 64,
        pairing_expires_at=now + timedelta(minutes=10),
    )
    from app.core.schemas.family import WebPushSubscription

    link = await repo.activate(
        link_id,
        push_subscription=WebPushSubscription.model_validate(PUSH_SUB),
        fallback_contact=FallbackContact(channel="SMS", value="0901234567"),
        consented_at=now,
    )
    assert link is not None and link.status is LinkStatus.ACTIVE
    assert link.borrower_display_label == "Mẹ"
    assert link.push_subscription is not None
    assert (
        await repo.activate(
            link_id, push_subscription=None, fallback_contact=None, consented_at=now
        )
        is None
    )
    # Endpoint/SĐT không nằm dạng rõ trong DB (mã hóa envelope).
    async with pg_admin.connect() as c:
        row = (
            await c.execute(
                text("SELECT webpush_subscription_cipher FROM family_guardian_links WHERE id = :i"),
                {"i": link_id},
            )
        ).one()
    assert b"fcm.googleapis.com" not in bytes(row[0])

    assert [lk.link_id for lk in await repo.list_live_for_device(device)] == [link_id]
    await repo.mark_push_expired(link_id)
    expired = await repo.get_link(link_id)
    assert expired is not None and expired.status is LinkStatus.PUSH_EXPIRED_410
    assert expired.push_subscription is None
    assert expired.fallback_contact is not None  # vẫn gửi được SMS
    assert [lk.link_id for lk in await repo.list_live_for_device(device)] == [link_id]


@pytest.mark.pg
async def test_ec_db11_pg_persistence_and_fallback_reader(
    bound_db: AsyncEngine, pg_admin: AsyncEngine
) -> None:
    device, sid = uuid.uuid4(), uuid.uuid4()
    async with pg_admin.begin() as c:
        await c.execute(
            text("INSERT INTO anonymous_devices (id, platform_type) VALUES (:d, 'WEB_PWA')"),
            {"d": device},
        )
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    fiber = LoanJourneyFiber(
        fiber_key="default",
        session_id=str(sid),
        state=HsmState.S4_SHIELD_ARMED_24H,
        entered_at_ms=now_ms,
        updated_at_ms=now_ms,
        shield_expires_at_ms=now_ms + 24 * 3_600_000,
        evidence_sources=frozenset({EvidenceSource.EDGE_NOTIF}),
    )
    reader = PgFallbackReader()
    assert await reader(str(device)) is None  # PG xác nhận rỗng ≠ lỗi
    await PgShieldPersistence().save(
        device,
        [fiber],
        sandbox_namespace="live",
        trigger_origin=TriggerOrigin.STATION_3_NOTIF,
        amount_bucket_tier=AmountBucketTier.TIER_3M_10M,
        disbursement_at=datetime.now(UTC),
        risk_log_odds=-0.5,
    )
    fibers = await reader(str(device))
    assert fibers is not None and len(fibers) == 1
    assert fibers[0].session_id == str(sid)
    assert fibers[0].state is HsmState.S4_SHIELD_ARMED_24H
    assert fibers[0].latest_disbursement_ms is not None
    other_ns = await PgFallbackReader("sbx_other")(str(device))
    assert other_ns is None  # cô lập sandbox_namespace
