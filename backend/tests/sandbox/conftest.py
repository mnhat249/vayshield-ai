"""Fixture Sandbox: app với dịch vụ realtime fake (không mạng) + persistence ghi lại để kiểm tra."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.clock import FakeClock
from app.core.config import AppEnv, Settings
from app.core.schemas.common import AmountBucketTier, TriggerOrigin
from app.services.realtime.container import RealtimeServices, build_services
from app.services.realtime.repository import InMemoryFamilyRepository
from app.services.realtime.senders import FakePushSender, FakeSmsSender
from app.shield_state.fibers import LoanJourneyFiber

# Giá trị giả chỉ dùng trong test (không phải credential thật).
VIP_CODE = "test-only-vip-code-0001"
SESSIONS = "/api/v1/sandbox/sessions"
SIMULATE = "/api/v1/sandbox/simulate-event"


class RecordingPersistence:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def save(
        self,
        device_id: UUID,
        fibers: Sequence[LoanJourneyFiber],
        *,
        sandbox_namespace: str,
        trigger_origin: TriggerOrigin | None,
        amount_bucket_tier: AmountBucketTier | None,
        disbursement_at: datetime | None,
        risk_log_odds: float,
    ) -> None:
        self.calls.append(
            {
                "device_id": device_id,
                "namespace": sandbox_namespace,
                "origin": trigger_origin,
                "fibers": list(fibers),
            }
        )


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env=AppEnv.TEST,
        database_url=None,
        redis_url=None,
        log_json=False,
        sandbox_vip_code=SecretStr(VIP_CODE),
        sandbox_daily_budget_usd=2.0,
        gemini_vip_daily_budget_usd=20.0,
    )


@pytest.fixture
def persistence() -> RecordingPersistence:
    return RecordingPersistence()


@pytest.fixture
async def services(
    app: FastAPI,
    redis: fakeredis.FakeAsyncRedis,
    fake_clock: FakeClock,
    settings: Settings,
    persistence: RecordingPersistence,
) -> AsyncIterator[RealtimeServices]:
    svc = build_services(
        redis=redis,
        clock=fake_clock,
        settings=settings,
        repo=InMemoryFamilyRepository(),
        push=FakePushSender(),
        sms=FakeSmsSender(),
        sweep_interval_s=None,
        persistence=persistence,
    )
    app.state.realtime_services = svc
    yield svc
    await svc.escalation.aclose()
    await svc.hub.stop()


class Judge:
    """Một 'tab' Sandbox: token + namespace riêng."""

    def __init__(self, client: httpx.AsyncClient, data: dict[str, Any]) -> None:
        self.client = client
        self.token: str = data["sandbox_session_token"]
        self.namespace: str = data["sandbox_namespace"]
        self.tier: str = data["tier"]
        self.data = data

    @property
    def headers(self) -> dict[str, str]:
        return {"X-Sandbox-Session": self.token}

    async def event(self, event_type: str, **params: Any) -> httpx.Response:
        return await self.client.post(
            SIMULATE, json={"event_type": event_type, "params": params}, headers=self.headers
        )


@pytest.fixture
async def new_judge(client: httpx.AsyncClient, services: RealtimeServices) -> Any:
    async def make(vip_code: str | None = None) -> Judge:
        r = await client.post(SESSIONS, json={"vip_code": vip_code} if vip_code else {})
        assert r.status_code == 201, r.text
        return Judge(client, r.json())

    return make
