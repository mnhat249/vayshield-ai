"""Fixture Copilot/Ops/Triage: JWT giả, kho in-memory, đồ thị mule có dữ liệu, phiên Sandbox giả."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI

from app.copilot.deps import get_ops_cipher, get_ops_repository
from app.copilot.ops_auth import (
    ROLE_FRAUD_ANALYST,
    InMemoryOpsUserDirectory,
    OpsPrincipal,
    OpsTokenService,
    get_ops_token_service,
)
from app.copilot.repository import InMemoryOpsRepository
from app.core.clock import FakeClock
from app.core.schemas.ops import EvidenceChannel, ScamTrap
from app.mule_graph.service import EMBEDDING_DIM, MuleGraphService
from app.security.blind_index import BlindIndexer
from app.security.envelope import EnvelopeCipher
from app.services.realtime.container import SandboxPrincipalResolver, get_sandbox_resolver
from app.services.realtime.models import BorrowerPrincipal
from tests.security._helpers import KEK_A, make_settings

JWT_SECRET = "test-only-ops-jwt-secret-" + "s" * 32  # GIẢ, không phải bí mật thật
JUDGE_CODE = "judge-code-test-only-0001"
ANALYST_USER = "analyst@example.test"
ANALYST_PASSWORD = "correct horse battery staple test"
VIEWER_USER = "viewer@example.test"
ACCOUNT_A = "1903123456788899"  # STK giả; cùng dải VA 8 số đầu với ACCOUNT_B
ACCOUNT_B = "1903123400001111"
BANK_BIN = "970407"
SANDBOX_HEADERS = {"X-Sandbox-Session": "sbx_test_session_token_0001"}


@pytest.fixture
def directory() -> InMemoryOpsUserDirectory:
    d = InMemoryOpsUserDirectory(iterations=1000)
    d.add(ANALYST_USER, ANALYST_PASSWORD, (ROLE_FRAUD_ANALYST,), salt=b"salt-analyst-test")
    d.add(VIEWER_USER, "viewer password test", ("VIEWER",), salt=b"salt-viewer-test")
    return d


def make_token_service(
    clock: FakeClock, directory: InMemoryOpsUserDirectory, judge_code: str | None = JUDGE_CODE
) -> OpsTokenService:
    return OpsTokenService(
        secret=JWT_SECRET,
        issuer="vayshield-ops",
        access_ttl_s=900,
        refresh_ttl_s=30 * 24 * 3600,
        clock=clock,
        directory=directory,
        judge_code=judge_code,
    )


@pytest.fixture
def tokens(fake_clock: FakeClock, directory: InMemoryOpsUserDirectory) -> OpsTokenService:
    return make_token_service(fake_clock, directory)


@pytest.fixture
def analyst_headers(tokens: OpsTokenService) -> dict[str, str]:
    t = tokens.issue(OpsPrincipal(ANALYST_USER, (ROLE_FRAUD_ANALYST,)))
    return {"Authorization": f"Bearer {t.access_token}"}


@pytest.fixture
def viewer_headers(tokens: OpsTokenService) -> dict[str, str]:
    t = tokens.issue(OpsPrincipal(VIEWER_USER, ("VIEWER",)))
    return {"Authorization": f"Bearer {t.access_token}"}


@pytest.fixture
def ops_repo() -> InMemoryOpsRepository:
    return InMemoryOpsRepository()


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(KEK_A)


@pytest.fixture
def ops_app(
    app: FastAPI,
    tokens: OpsTokenService,
    ops_repo: InMemoryOpsRepository,
    cipher: EnvelopeCipher,
) -> FastAPI:
    app.dependency_overrides[get_ops_token_service] = lambda: tokens
    app.dependency_overrides[get_ops_repository] = lambda: ops_repo
    app.dependency_overrides[get_ops_cipher] = lambda: cipher
    return app


def vec(axis: int) -> tuple[float, ...]:
    v = [0.0] * EMBEDDING_DIM
    v[axis] = 1.0
    return tuple(v)


class Seeded:
    def __init__(self, node_a: UUID, node_b: UUID, cluster_id: UUID, devices: list[UUID]) -> None:
        self.node_a = node_a
        self.node_b = node_b
        self.cluster_id = cluster_id
        self.devices = devices


@pytest.fixture
async def seeded(
    ops_repo: InMemoryOpsRepository, cipher: EnvelopeCipher, fake_clock: FakeClock
) -> Seeded:
    """2 STK cùng dải VA, 3 thiết bị độc lập -> 1 cụm CRITICAL_WAVE."""
    indexer = BlindIndexer.from_settings(make_settings())
    svc = MuleGraphService(ops_repo.graph, indexer, cipher)
    devices = [uuid4(), uuid4(), uuid4()]
    now = fake_clock.now()
    lender = uuid4()
    ops_repo.lenders[lender] = "Home Credit"
    first = None
    for i, (acct, dev, ch) in enumerate(
        [
            (ACCOUNT_A, devices[0], EvidenceChannel.VIETQR_CRYPTO_DECODED),
            (ACCOUNT_A, devices[1], EvidenceChannel.OCR_RECEIPT_EXTRACTED),
            (ACCOUNT_B, devices[0], EvidenceChannel.VOICE_CHAT_REPORTED),
            (ACCOUNT_B, devices[2], EvidenceChannel.VIETQR_CRYPTO_DECODED),
        ]
    ):
        r = await svc.report_mule_account(
            bank_bin=BANK_BIN,
            account_no=acct,
            reporter_device_id=dev,
            channel=ch,
            now=now + timedelta(minutes=i),
            scam_script_vec=vec(0),
            scam_trap=ScamTrap.TRAP_1_ADVANCE_FEE,
            impersonated_lender_id=lender,
        )
        first = first or r
    nodes = list(ops_repo.graph.nodes.values())
    by_tail = {n.masked_account_display: n.id for n in nodes}
    cluster_id = next(iter(ops_repo.graph.clusters))
    return Seeded(by_tail["***899"], by_tail["***111"], cluster_id, devices)


@pytest.fixture
def sandbox_app(app: FastAPI) -> FastAPI:
    async def resolver(_token: str) -> BorrowerPrincipal | None:
        return BorrowerPrincipal(device_id=uuid4(), sandbox_namespace="sbx_test")

    def provide() -> SandboxPrincipalResolver:
        return resolver

    app.dependency_overrides[get_sandbox_resolver] = provide
    return app
