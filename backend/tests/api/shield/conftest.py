"""Fixture Trạm 3: dịch vụ realtime với fake (push/SMS/hẹn giờ) gắn vào app."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis
import pytest
from fastapi import FastAPI

from app.core.clock import FakeClock
from app.core.config import Settings
from app.security.device_auth import get_device_key_resolver
from app.services.realtime.container import RealtimeServices, build_services
from app.services.realtime.repository import InMemoryFamilyRepository
from app.services.realtime.senders import FakePushSender, FakeSmsSender

from ._helpers import ManualSleeper, key_resolver


@pytest.fixture
def push() -> FakePushSender:
    return FakePushSender()


@pytest.fixture
def sms() -> FakeSmsSender:
    return FakeSmsSender()


@pytest.fixture
def sleeper(fake_clock: FakeClock) -> ManualSleeper:
    return ManualSleeper(fake_clock)


@pytest.fixture
def repo() -> InMemoryFamilyRepository:
    return InMemoryFamilyRepository()


@pytest.fixture
async def services(
    app: FastAPI,
    redis: fakeredis.FakeAsyncRedis,
    fake_clock: FakeClock,
    settings: Settings,
    push: FakePushSender,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
    repo: InMemoryFamilyRepository,
) -> AsyncIterator[RealtimeServices]:
    svc = build_services(
        redis=redis,
        clock=fake_clock,
        settings=settings,
        repo=repo,
        push=push,
        sms=sms,
        sleeper=sleeper.sleep,
        sweep_interval_s=None,
    )
    app.state.realtime_services = svc
    app.dependency_overrides[get_device_key_resolver] = lambda: key_resolver
    yield svc
    await svc.escalation.aclose()
    await svc.hub.stop()
