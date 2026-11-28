"""PostgreSQL thật (CI, ``@pytest.mark.pg``): kho thiết bị + audit đã che PII (EC-DB01/18/21)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.core import db as core_db
from app.core.schemas.common import TriggerOrigin
from app.services.audit_pipeline.apk import load_apk_trust
from app.services.audit_pipeline.compute import assess
from app.services.audit_pipeline.facts import FeeFact, LoanFacts
from app.services.audit_pipeline.records import build_record
from app.services.audit_pipeline.repository import (
    DeviceRecord,
    SqlAuditRepository,
    SqlDeviceRepository,
)
from app.services.realtime.models import BorrowerPrincipal
from app.verdict import InMemoryLenderRegistry
from tests.verdict._helpers import ALL_RECORDS


@pytest.fixture
async def bound_db(pg: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    core_db._engine = pg
    core_db._sessionmaker = async_sessionmaker(pg, expire_on_commit=False)
    try:
        yield pg
    finally:
        core_db._engine = None
        core_db._sessionmaker = None


def _device(phone: str | None = None) -> DeviceRecord:
    now = datetime.now(UTC)
    return DeviceRecord(
        device_id=uuid.uuid4(),
        pubkey_spki=b"\x30" * 91,
        platform_type="ANDROID_NATIVE",
        daily_wage_vnd=350_000,
        policy_version=1,
        phone_blind_hmac=phone,
        pepper_version=1,
        phone_optin_exp_at=(now + timedelta(days=90)) if phone else None,
    )


@pytest.mark.pg
async def test_device_roundtrip_and_phone_not_hijacked(bound_db: AsyncEngine) -> None:
    repo = SqlDeviceRepository()
    phone = uuid.uuid4().hex * 2
    first, second = _device(phone), _device(phone)
    assert await repo.create(first) is True
    assert await repo.create(second) is False  # EC-DB03: không chiếm SĐT của thiết bị khác
    got = await repo.get(first.device_id)
    assert got is not None and got.daily_wage_vnd == 350_000 and got.phone_blind_hmac == phone
    other = await repo.get(second.device_id)
    assert other is not None and other.phone_blind_hmac is None


@pytest.mark.pg
async def test_audit_save_get_update_isolated_per_device_and_namespace(
    bound_db: AsyncEngine,
) -> None:
    devices = SqlDeviceRepository()
    mine, theirs = _device(), _device()
    await devices.create(mine)
    await devices.create(theirs)
    facts = LoanFacts(
        claimed_lender_name="App Vay Nhanh \x00A",
        nominal_principal_vnd=10_000_000,
        net_disbursed_vnd=8_500_000,
        total_repayment_vnd=12_000_000,
        tenure_days=90,
        fees=(FeeFact("Phí hồ sơ", 1_500_000),),
    )
    assessment = assess(facts, InMemoryLenderRegistry(ALL_RECORDS))
    audit_id = uuid.uuid4()
    principal = BorrowerPrincipal(device_id=mine.device_id)
    record = build_record(
        audit_id=audit_id,
        principal=principal,
        fiber_key="default",
        origin=TriggerOrigin.STATION_2_SCAN,
        image_count=2,
        facts=facts,
        assessment=assessment,
        wage_vnd=350_000,
        qr_found=False,
        cited=("BLDS_2015_D468",),
        latency_ms=1234,
        model_tier="gemini-2.5-flash",
    )
    repo = SqlAuditRepository()
    await repo.save(record)
    loaded = await repo.get(audit_id, mine.device_id, "live")
    assert loaded is not None
    assert loaded.verdict_code == record.verdict_code
    assert "\x00" not in str(loaded.context)  # EC-DB26
    assert loaded.context["wage"] == 350_000
    assert await repo.get(audit_id, theirs.device_id, "live") is None
    assert await repo.get(audit_id, mine.device_id, "sbx_other") is None

    edited = build_record(
        audit_id=audit_id,
        principal=principal,
        fiber_key="default",
        origin=TriggerOrigin.STATION_2_SCAN,
        image_count=2,
        facts=facts.with_user_numbers(
            nominal_principal_vnd=10_000_000,
            net_disbursed_vnd=9_500_000,
            total_repayment_vnd=10_500_000,
            tenure_days=365,
            regimes=[],
            deducted_fees_vnd=0,
            upfront_fee_amount_vnd=0,
        ),
        assessment=assessment,
        wage_vnd=350_000,
        qr_found=False,
        cited=(),
        latency_ms=1234,
        model_tier="gemini-2.5-flash",
        user_edited=True,
    )
    await repo.update_recalculated(edited)
    again = await repo.get(audit_id, mine.device_id, "live")
    assert again is not None and again.user_edited_numbers is True
    assert again.net_disbursed_vnd == 9_500_000


@pytest.mark.pg
async def test_apk_trust_loaded_from_registry_jsonb(
    bound_db: AsyncEngine, pg_admin: AsyncEngine
) -> None:
    code = f"TEST_{uuid.uuid4().hex[:8]}"
    async with pg_admin.begin() as c:
        await c.execute(
            text(
                "INSERT INTO licensed_lender_registry (entity_code, legal_category,"
                " official_short_name, official_legal_name, normalized_name, android_packages)"
                " VALUES (:c, 'LICENSED_FINCO', 'T', 'T Co', 't', CAST(:j AS JSONB))"
            ),
            {"c": code, "j": '[{"packageName": "vn.test.app", "signingCertSha256": "AB12"}]'},
        )
    trust = await load_apk_trust(bound_db)
    entry = trust.get("vn.test.app")
    assert entry is not None and entry.entity_code == code
    assert entry.cert_sha256 == frozenset({"ab12"})
