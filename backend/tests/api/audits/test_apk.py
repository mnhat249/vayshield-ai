"""POST /audits/apk-verify (Trạm 1) + liên kết nguồn gốc APK sang Trạm 2."""

from __future__ import annotations

from typing import Any

import httpx

from ai_pipeline.extraction.gemini_client import reply_json
from app.core.clock import FakeClock
from app.services.audit_pipeline.apk import ApkTrustEntry, ApkTrustStore
from tests.ai_pipeline.extraction._factory import PRIMARY, loan
from tests.api.shield._helpers import DEVICE_A, DEVICE_B, signed_post

from ._helpers import APK, STREAM, image_part, jpeg, meta, parse_sse, signed_multipart

OFFICIAL_SHA = "ab" * 32
FAKE_SHA = "cd" * 32
PKG = "vn.homecredit.demo"  # trùng gói trong registry giả của tests/verdict/_helpers


def _trust(sha: frozenset[str] = frozenset({OFFICIAL_SHA})) -> ApkTrustStore:
    return ApkTrustStore({PKG: ApkTrustEntry("FINCO_HOMECREDIT", sha)})


def _req(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "package_name": PKG,
        "signing_cert_sha256": OFFICIAL_SHA,
        "app_label": "Home Credit",
        "installer_package": "com.android.vending",
        "requested_permissions": [],
    }
    body.update(over)
    return body


async def test_official_signature_match_is_licensed(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env(trust=_trust())
    r = await signed_post(client, fake_clock, APK, _req())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["apk_verdict"] == "OFFICIAL_LICENSED" and data["severity"] == "GREEN"
    assert data["matched_lender"]["entity_code"] == "FINCO_HOMECREDIT"
    assert data["recommend_contract_scan"] is False


async def test_same_package_other_certificate_is_lookalike_spoof(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env(trust=_trust())
    r = await signed_post(client, fake_clock, APK, _req(signing_cert_sha256=FAKE_SHA))
    data = r.json()
    assert data["apk_verdict"] == "LOOKALIKE_SPOOF" and data["severity"] == "CRITICAL_RED"
    assert data["lookalike_of"] == "Home Credit"
    assert data["matched_lender"] is None and data["recommend_contract_scan"] is True


async def test_known_package_without_official_cert_is_never_called_safe(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env(trust=_trust(frozenset()))
    data = (await signed_post(client, fake_clock, APK, _req())).json()
    assert data["apk_verdict"] == "UNKNOWN_LOW_RISK" and data["severity"] == "AMBER"


async def test_brand_named_app_with_unknown_package_is_spoof(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env(trust=_trust())
    data = (
        await signed_post(
            client, fake_clock, APK, _req(package_name="com.fake.loan", app_label="Home Credit VN")
        )
    ).json()
    assert data["apk_verdict"] == "LOOKALIKE_SPOOF"


async def test_sideload_with_contacts_and_photos_is_high_risk(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env()
    data = (
        await signed_post(
            client,
            fake_clock,
            APK,
            _req(
                package_name="com.cash.fast",
                app_label="Cash Fast",
                installer_package=None,
                requested_permissions=["READ_CONTACTS", "android.permission.READ_MEDIA_IMAGES"],
            ),
        )
    ).json()
    assert data["apk_verdict"] == "UNLICENSED_HIGH_RISK"
    assert data["dangerous_permissions"] == [
        "android.permission.READ_CONTACTS",
        "android.permission.READ_MEDIA_IMAGES",
    ]


async def test_play_store_unknown_app_low_risk(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env()
    data = (
        await signed_post(
            client, fake_clock, APK, _req(package_name="com.cash.fast", app_label="Cash Fast")
        )
    ).json()
    assert data["apk_verdict"] == "UNKNOWN_LOW_RISK"


async def test_apk_verify_requires_valid_device_signature(
    env: Any, client: httpx.AsyncClient
) -> None:
    missing = await client.post(APK, json=_req())
    assert missing.status_code == 401
    forged = await client.post(
        APK,
        json=_req(),
        headers={"X-VayShield-Device-Signature": f"v1;device_id={DEVICE_A};ts=1;sig=" + "A" * 86},
    )
    assert forged.status_code == 401


async def test_linked_apk_signature_mismatch_drives_brand_spoof_verdict(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Chữ ký APK sai (server nhớ kết quả Trạm 1) là tín hiệu độc lập → V_RED_BRAND_SPOOF."""
    build_env(
        trust=_trust(),
        script={PRIMARY: [reply_json(loan(claimed_lender_name="Home Credit"))]},
    )
    apk = (await signed_post(client, fake_clock, APK, _req(signing_cert_sha256=FAKE_SHA))).json()
    fake_clock.advance(1)
    resp = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(jpeg())],
        data={"metadata_json": meta(linked_apk_audit_id=apk["audit_id"])},
    )
    verdict = next(
        f["data"] for f in parse_sse(resp.text) if f["event"] == "audit.verdict_computed"
    )
    assert verdict["verdict_code"] == "V_RED_BRAND_SPOOF"
    assert "APK_SIGNATURE_MISMATCH" in verdict["contradictions"]


async def test_linked_apk_of_another_device_is_ignored(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env(
        trust=_trust(),
        script={PRIMARY: [reply_json(loan(claimed_lender_name="Home Credit"))]},
    )
    apk = (
        await signed_post(
            client, fake_clock, APK, _req(signing_cert_sha256=FAKE_SHA), device=DEVICE_B
        )
    ).json()
    fake_clock.advance(1)
    resp = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(jpeg())],
        data={"metadata_json": meta(linked_apk_audit_id=apk["audit_id"])},
    )
    verdict = next(
        f["data"] for f in parse_sse(resp.text) if f["event"] == "audit.verdict_computed"
    )
    assert "APK_SIGNATURE_MISMATCH" not in verdict["contradictions"]
