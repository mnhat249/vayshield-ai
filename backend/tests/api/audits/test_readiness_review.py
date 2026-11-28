"""Review T11: registry + sổ chữ ký APK nạp nguyên tử, TTL, trust rỗng không gây oan."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.clock import FakeClock
from tests.api.shield._helpers import signed_post

from ._helpers import APK

REQ = {
    "package_name": "vn.homecredit.real",
    "signing_cert_sha256": "ab" * 32,
    "app_label": "Home Credit",
    "installer_package": "com.android.vending",
    "requested_permissions": [],
}


async def test_empty_trust_store_never_turns_real_app_into_spoof(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    calls: list[str] = []

    class Hook:
        async def on_apk(self, *a: object) -> None:
            calls.append("e1")

        async def on_submitted(self, *a: object) -> None: ...
        async def on_verdict(self, *a: object) -> None: ...

    build_env(shield=Hook())  # trust rỗng (lỗi nạp / chưa có chứng chỉ)
    data = (await signed_post(client, fake_clock, APK, REQ)).json()
    assert data["apk_verdict"] == "UNKNOWN_LOW_RISK"
    assert data["lookalike_of"] is None
    assert calls == []  # không bắn E1 vào HSM


async def test_trust_not_loaded_means_not_ready_503(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    e = build_env()
    e.services.trust_loaded = False
    e.services.refresher = None
    assert e.services.readiness()["apk_trust"] is False
    r = await signed_post(client, fake_clock, APK, REQ)
    assert r.status_code == 503


async def test_failed_refresh_keeps_not_ready_until_both_snapshots_load(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    e = build_env()
    e.services.trust_loaded = False
    state = {"n": 0}

    async def refresh() -> None:
        state["n"] += 1
        if state["n"] == 1:
            raise ConnectionError("trust load failed")
        e.services.trust_loaded = True

    e.services.refresher = refresh
    assert (await signed_post(client, fake_clock, APK, REQ)).status_code == 503
    assert e.services.loaded is False
    fake_clock.advance(2)
    assert (await signed_post(client, fake_clock, APK, REQ)).status_code == 200


async def test_periodic_refresh_after_ttl_and_failure_keeps_old_snapshot(
    build_env: Any,
) -> None:
    e = build_env()
    calls = {"n": 0}

    async def refresh() -> None:
        calls["n"] += 1
        raise ConnectionError("db blip")

    svc = e.services
    svc.refresher = refresh
    svc.refresh_ttl_s = 300
    await svc.ensure_ready()  # đặt mốc đầu
    assert calls["n"] == 0
    svc.clock.advance(301)
    await svc.ensure_ready()
    assert svc._bg_refresh is not None
    await svc._bg_refresh
    assert calls["n"] == 1
    assert svc.loaded is True  # snapshot cũ vẫn phục vụ
    await svc.ensure_ready()  # chưa qua TTL mới → không nạp lại ngay
    await asyncio.sleep(0)
    assert calls["n"] == 1
