"""Review T11: registry + sổ chữ ký APK nạp nguyên tử, TTL, trust rỗng không gây oan."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.clock import FakeClock
from tests.api.shield._helpers import DEVICE_A, signed_post

from ._helpers import APK

REQ = {
    "package_name": "vn.homecredit.demo",
    "signing_cert_sha256": "ab" * 32,
    "app_label": "Home Credit",
    "installer_package": "com.android.vending",
    "requested_permissions": [],
}


async def test_official_package_without_cert_is_unknown_not_spoof(
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


async def test_lifespan_uses_create_app_settings_and_starts_audit_pipeline(
    monkeypatch: Any, redis: Any
) -> None:
    from pydantic import SecretStr

    from app import main
    from app.core.config import AppEnv, Settings

    seen: list[Any] = []

    async def fake_start(app: Any, settings: Any = None) -> None:
        seen.append(settings)

    monkeypatch.setattr(main, "start_audit_pipeline", fake_start)
    cfg = Settings(
        app_env=AppEnv.TEST,
        log_json=False,
        database_url=SecretStr("postgresql+asyncpg://u:p@localhost:1/none"),
    )
    app = main.create_app(cfg)
    async with app.router.lifespan_context(app):
        pass
    assert seen == [cfg]


async def test_missing_critical_page_flag_survives_recalculate(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Nhập tay 3 ô không được gỡ cờ thiếu trang (không thể ra GREEN)."""
    from ai_pipeline.extraction.gemini_client import reply_json
    from tests.ai_pipeline.extraction._factory import PRIMARY, loan

    from ._helpers import STREAM, image_part, jpeg, meta, parse_sse, signed_multipart

    build_env(script={PRIMARY: [reply_json(loan(missing_critical_page_suspected=True))]})
    resp = await signed_multipart(
        client, fake_clock, STREAM, files=[image_part(jpeg())], data={"metadata_json": meta()}
    )
    audit_id = parse_sse(resp.text)[0]["data"]["audit_id"]
    r = await signed_post(
        client,
        fake_clock,
        f"/api/v1/audits/{audit_id}/recalculate",
        {
            "nominal_principal_vnd": 10_000_000,
            "net_disbursed_vnd": 9_900_000,
            "total_repayment_vnd": 10_000_000,
            "tenure_days": 365,
        },
    )
    verdict = r.json()["verdict"]
    assert "MISSING_CRITICAL_PAGE" in verdict["contradictions"]
    assert verdict["verdict_code"] != "V_GREEN_TRANSPARENT"


async def test_policy_etag_changes_after_bundle_expiry_and_stable_within_day(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:

    from tests.api.shield._helpers import H, sign

    build_env()

    async def get(**extra: str) -> httpx.Response:
        signed = sign(fake_clock, DEVICE_A, "GET", "/api/v1/policies/latest", b"")
        return await client.get("/api/v1/policies/latest", headers={H: signed[H], **extra})

    first = (await get()).headers["etag"]
    fake_clock.advance(3600)
    assert (await get(**{"If-None-Match": first})).status_code == 304  # cùng ngày: 304
    fake_clock.advance(8 * 24 * 3600)  # quá hạn 7 ngày của bundle cũ
    again = await get(**{"If-None-Match": first})
    assert again.status_code == 200 and again.headers["etag"] != first


async def test_unknown_package_with_licensed_brand_label_is_red_even_without_certs(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Quyết định orchestrator: gói LẠ + nhãn thương hiệu cấp phép → đỏ dù trust thiếu cert."""
    build_env()  # trust rỗng, registry có Home Credit với gói vn.homecredit.demo
    fake = {**REQ, "package_name": "com.cash.fake", "installer_package": None}
    data = (await signed_post(client, fake_clock, APK, fake)).json()
    assert data["apk_verdict"] == "LOOKALIKE_SPOOF" and data["severity"] == "CRITICAL_RED"
    assert data["lookalike_of"] == "Home Credit"


async def test_truncated_jpeg_is_422_json_not_500(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """EC-DB05: JPEG header hợp lệ nhưng thân cắt cụt → 422 CORRUPT_IMAGE, không 500."""
    from ._helpers import STREAM, image_part, jpeg, meta, signed_multipart

    good = jpeg()
    r = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(good[: len(good) // 3])],
        data={"metadata_json": meta()},
    )
    assert r.status_code == 422, r.text
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_drain_background_waits_for_pending_work_and_logs_failures() -> None:
    from app.services.audit_pipeline.stream import _BACKGROUND, drain_background, spawn

    done: list[str] = []

    async def work() -> None:
        await asyncio.sleep(0.05)
        done.append("ok")

    async def boom() -> None:
        raise RuntimeError("bg failed")

    spawn(work())
    spawn(boom())
    await drain_background(2.0)
    assert done == ["ok"] and not _BACKGROUND
