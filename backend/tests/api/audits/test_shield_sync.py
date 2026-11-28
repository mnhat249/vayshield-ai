"""Đồng bộ HSM từ Trạm 1/2 (File 2 §2.1): E2 → V1/V2, E1 — best-effort, không phá audit."""

from __future__ import annotations

from typing import Any

import fakeredis
import httpx

from ai_pipeline.extraction.gemini_client import reply_json
from app.core.clock import FakeClock
from app.core.schemas.common import HsmState
from app.shield_state.store import ShieldStateStore
from tests.ai_pipeline.extraction._factory import PRIMARY, loan
from tests.api.shield._helpers import DEVICE_A, signed_post

from ._helpers import APK, STREAM, image_part, jpeg, meta, parse_sse, phases, signed_multipart


async def _states(redis: fakeredis.FakeAsyncRedis, clock: FakeClock) -> set[HsmState]:
    snap = await ShieldStateStore(redis).load(str(DEVICE_A), int(clock.now().timestamp() * 1000))
    return {f.state for f in snap.fibers}


async def test_advance_fee_verdict_hard_stops_the_fiber(
    build_env: Any,
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scam = loan(upfront_fee_demand={"demanded": True, "amount_vnd": 2_000_000}, net_disbursed_vnd=0)
    build_env(script={PRIMARY: [reply_json(scam)]})
    resp = await signed_multipart(
        client, fake_clock, STREAM, files=[image_part(jpeg())], data={"metadata_json": meta()}
    )
    assert phases(parse_sse(resp.text))[-1] == "audit.legal_explained"
    assert HsmState.S3_ADVANCE_FEE_HARD_STOP in await _states(redis, fake_clock)


async def test_ordinary_verdict_pre_arms_shield_s4(
    env: Any,
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    resp = await signed_multipart(
        client, fake_clock, STREAM, files=[image_part(jpeg())], data={"metadata_json": meta()}
    )
    assert resp.status_code == 200
    assert HsmState.S4_SHIELD_ARMED_24H in await _states(redis, fake_clock)


async def test_shield_hook_failure_never_breaks_the_audit(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    class Broken:
        async def on_submitted(self, *_: object) -> None:
            raise RuntimeError("redis down")

        async def on_verdict(self, *_: object) -> None:
            raise RuntimeError("redis down")

        async def on_apk(self, *_: object) -> None:
            raise RuntimeError("redis down")

    build_env(shield=Broken())
    resp = await signed_multipart(
        client, fake_clock, STREAM, files=[image_part(jpeg())], data={"metadata_json": meta()}
    )
    assert phases(parse_sse(resp.text))[-1] == "audit.legal_explained"
    fake_clock.advance(1)
    apk = await signed_post(
        client,
        fake_clock,
        APK,
        {
            "package_name": "com.cash.fast",
            "signing_cert_sha256": "ab" * 32,
            "installer_package": None,
            "requested_permissions": ["READ_CONTACTS", "READ_MEDIA_IMAGES"],
        },
    )
    assert apk.status_code == 200 and apk.json()["apk_verdict"] == "UNLICENSED_HIGH_RISK"
