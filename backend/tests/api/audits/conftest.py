"""Fixture Trạm 1 & 2: dịch vụ audit với fake (Gemini, STT, legal) gắn vào app, không mạng/DB."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import fakeredis
import pytest
from fastapi import FastAPI

from ai_pipeline.extraction import ExtractionPipeline
from ai_pipeline.extraction.gemini_client import FakeGeminiClient, FakeReply, reply_json
from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.schemas.common import CitedClause
from app.security.device_auth import get_device_key_resolver
from app.security.signing import Ed25519Signer
from app.services.audit_pipeline.apk import ApkTrustStore
from app.services.audit_pipeline.container import AuditServices, build_services
from app.services.audit_pipeline.repository import InMemoryAuditRepository
from app.verdict import InMemoryLenderRegistry
from tests.ai_pipeline.extraction._factory import PRIMARY, loan, make_cascade
from tests.api.shield._helpers import key_resolver
from tests.security._helpers import make_settings
from tests.verdict._helpers import ALL_RECORDS


class FakeLegal:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def cited_clauses(self, verdict_code: str) -> tuple[CitedClause, ...]:
        self.calls.append(verdict_code)
        return (CitedClause(code="BLDS_2015_D468", title="Điều 468 BLDS 2015 (test)"),)


class FakeStt:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def transcribe(self, audio: bytes, mime_type: str, locale: str) -> str:
        self.calls += 1
        return self.text


class Env:
    """Gói dịch vụ + fake để test truy cập (gemini client, repo, legal...)."""

    def __init__(self, services: AuditServices, gemini: FakeGeminiClient, legal: FakeLegal) -> None:
        self.services = services
        self.gemini = gemini
        self.legal = legal
        self.audits: InMemoryAuditRepository = services.audits  # type: ignore[assignment]


BuildEnv = Callable[..., Env]


@pytest.fixture
def registry() -> InMemoryLenderRegistry:
    return InMemoryLenderRegistry(ALL_RECORDS)


@pytest.fixture
def audit_settings() -> Settings:
    return make_settings()


@pytest.fixture
def build_env(
    app: FastAPI,
    redis: fakeredis.FakeAsyncRedis,
    fake_clock: FakeClock,
    audit_settings: Settings,
    registry: InMemoryLenderRegistry,
) -> BuildEnv:
    def _build(
        *,
        script: dict[str, list[FakeReply]] | None = None,
        settings: Settings | None = None,
        failover: float = 0.2,
        soft: float = 0.05,
        no_pipeline: bool = False,
        trust: ApkTrustStore | None = None,
        **overrides: Any,
    ) -> Env:
        cfg = settings or audit_settings
        cascade, client, _ = make_cascade(
            script if script is not None else {PRIMARY: [reply_json(loan())]},
            soft=soft,
            failover=failover,
        )
        legal = FakeLegal()
        kwargs: dict[str, Any] = {
            "settings": cfg,
            "clock": fake_clock,
            "redis": redis,
            "registry": registry,
            "trust": trust or ApkTrustStore(),
            "audits": InMemoryAuditRepository(),
            "pipeline": None if no_pipeline else ExtractionPipeline(cascade),
            "legal": legal,
            "signer": Ed25519Signer.from_settings(cfg),
        }
        kwargs.update(overrides)
        services = build_services(**kwargs)
        app.state.audit_services = services
        app.dependency_overrides[get_device_key_resolver] = lambda: key_resolver
        return Env(services, client, legal)

    return _build


@pytest.fixture
def env(build_env: BuildEnv) -> Env:
    return build_env()
