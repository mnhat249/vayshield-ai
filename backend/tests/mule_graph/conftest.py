"""Fixture cho test đồ thị Mule + webhook B2B (không DB/Redis thật)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import fakeredis
import pytest
from fastapi import FastAPI

from app.api.routes.b2b import get_b2b_repository
from app.core.clock import FakeClock
from app.core.config import Settings
from app.mule_graph.models import DeviceMatch, PartnerRecord
from app.mule_graph.repository import InMemoryRepository
from app.mule_graph.service import MuleGraphService
from app.security.blind_index import BlindIndexer, BlindIndexKind
from app.security.envelope import EnvelopeCipher
from app.security.partner_hmac import compute_partner_signature
from app.shield_state.store import ShieldStateStore
from tests.security._helpers import KEK_A, make_settings

PARTNER_KEY = "vsp_test_partnerA"
SECRET_A = b"partner-a-secret-" + b"a" * 24
SECRET_B = b"partner-b-secret-" + b"b" * 24
PHONE_E164 = "+84909123456"  # SĐT giả cho test


@pytest.fixture
def mg_settings() -> Settings:
    return make_settings()


@pytest.fixture
def indexer(mg_settings: Settings) -> BlindIndexer:
    return BlindIndexer.from_settings(mg_settings)


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(KEK_A)


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def mule_service(
    repo: InMemoryRepository, indexer: BlindIndexer, cipher: EnvelopeCipher
) -> MuleGraphService:
    return MuleGraphService(repo, indexer, cipher)


@pytest.fixture
def partner_a(repo: InMemoryRepository) -> PartnerRecord:
    p = PartnerRecord(id=uuid4(), hmac_secret=SECRET_A, rate_limit_rpm=600)
    repo.partners[PARTNER_KEY] = p
    return p


@pytest.fixture
def partner_b(repo: InMemoryRepository) -> PartnerRecord:
    p = PartnerRecord(id=uuid4(), hmac_secret=SECRET_B, rate_limit_rpm=600)
    repo.partners["vsp_test_partnerB"] = p
    return p


@pytest.fixture
def phone_hmac(indexer: BlindIndexer) -> str:
    return indexer.index(BlindIndexKind.PHONE, PHONE_E164)


@pytest.fixture
def device_id(repo: InMemoryRepository, phone_hmac: str, fake_clock: FakeClock) -> UUID:
    did = uuid4()
    repo.devices_by_phone[phone_hmac] = DeviceMatch(
        device_id=did, phone_optin_exp_at=fake_clock.now() + timedelta(days=30)
    )
    return did


@pytest.fixture
def store(redis: fakeredis.FakeAsyncRedis) -> ShieldStateStore:
    return ShieldStateStore(redis)


@pytest.fixture
def b2b_app(app: FastAPI, repo: InMemoryRepository) -> FastAPI:
    app.dependency_overrides[get_b2b_repository] = lambda: repo
    return app


def body_json(phone_hmac: str, **over: object) -> bytes:
    data: dict[str, object] = {
        "borrower_phone_blind_hmac": phone_hmac,
        "amount_bucket_tier": "TIER_10M_30M",
        "product_type": "CASH_CONSUMER_LOAN",
        "shield_duration_hours": 24,
    }
    data.update(over)
    return json.dumps(data, separators=(",", ":")).encode()


def sign_headers(
    *,
    secret: bytes,
    now: datetime,
    raw: bytes,
    key: str = "idem-key-0001",
    partner_key: str = PARTNER_KEY,
    skew_s: int = 0,
) -> dict[str, str]:
    ts = str(int(now.timestamp()) + skew_s)
    return {
        "X-VayShield-Partner-Key": partner_key,
        "X-VayShield-Timestamp": ts,
        "X-VayShield-Signature": compute_partner_signature(secret, ts, raw),
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    }
