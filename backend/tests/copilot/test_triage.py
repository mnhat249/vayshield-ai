"""S2-EC48: cấp cứu Giờ Vàng S9 — tra BIN, hotline phong tỏa, đơn trình báo (JSON/PDF), < 120ms."""

from __future__ import annotations

import statistics
import time
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.core.clock import FakeClock
from app.triage.hotlines import (
    BankHotline,
    BankHotlineRegistry,
    InMemoryBankHotlineRegistry,
    get_bank_hotline_registry,
)

from .conftest import SANDBOX_HEADERS

URL = "/api/v1/triage/golden-hour-dossier"
RAW_ACCOUNT = "1903123456788899"
# Số GIẢ chỉ dùng trong test (không phải tổng đài thật).
FAKE_REGISTRY = InMemoryBankHotlineRegistry(
    [
        BankHotline("970001", "BankTest A", "1800 000 001", verified=True),
        BankHotline("970002", "BankTest B", "1800 000 002", verified=False),
    ]
)


@pytest.fixture
def triage_app(sandbox_app: FastAPI) -> FastAPI:
    def provide() -> BankHotlineRegistry:
        return FAKE_REGISTRY

    sandbox_app.dependency_overrides[get_bank_hotline_registry] = provide
    return sandbox_app


def _body(clock: FakeClock, minutes_ago: int = 12, **over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "victim_bank_bin": "970001",
        "mule_bank_bin": "970002",
        "mule_masked_account": "***899",
        "transferred_at": (clock.now() - timedelta(minutes=minutes_ago)).isoformat(),
        "amount_bucket_tier": "TIER_10M_30M",
        "scam_channel": "ZALO_OR_OTT",
    }
    body.update(over)
    return body


async def test_s2_ec48_requires_auth(client: httpx.AsyncClient, fake_clock: FakeClock) -> None:
    r = await client.post(URL, json=_body(fake_clock))
    assert r.status_code == 401


async def test_s2_ec48_bin_lookup_returns_freeze_hotlines_and_countdown(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await client.post(URL, json=_body(fake_clock), headers=SANDBOX_HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["minutes_since_transfer"] == 12
    assert d["golden_hour_remaining_minutes"] == 48
    assert [h["bank_bin"] for h in d["bank_freeze_hotlines"]] == ["970001", "970002"]
    assert d["bank_freeze_hotlines"][0]["hotline"] == "1800 000 001"
    assert d["bank_freeze_hotlines"][0]["verified"] is True
    assert d["bank_freeze_hotlines"][1]["verified"] is False
    assert (
        d["bank_freeze_hotlines"][1]["hotline"] == ""
    )  # registry chưa verified -> không hiển thị số
    assert d["police_hotline"] == "113"
    assert d["pdf_available"] is True
    assert any("Không chuyển thêm" in s for s in d["steps_vi"])
    assert r.headers["cache-control"] == "no-store"


async def test_s2_ec48_unknown_bin_never_invents_a_hotline(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await client.post(
        URL,
        json=_body(fake_clock, victim_bank_bin="970999", mule_bank_bin=None),
        headers=SANDBOX_HEADERS,
    )
    d = r.json()
    (entry,) = d["bank_freeze_hotlines"]
    assert entry["hotline"] == "" and entry["verified"] is False
    assert any("mặt sau thẻ" in s for s in d["steps_vi"])


async def test_s2_ec48_no_bin_at_all_still_gives_steps(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await client.post(
        URL,
        json=_body(fake_clock, victim_bank_bin=None, mule_bank_bin=None, mule_masked_account=None),
        headers=SANDBOX_HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["bank_freeze_hotlines"][0]["hotline"] == ""
    assert d["steps_vi"]


async def test_s2_ec48_after_golden_hour_remaining_is_zero_with_urgent_step(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await client.post(URL, json=_body(fake_clock, minutes_ago=300), headers=SANDBOX_HEADERS)
    d = r.json()
    assert d["minutes_since_transfer"] == 300
    assert d["golden_hour_remaining_minutes"] == 0
    assert "quá 1 giờ" in d["steps_vi"][0]


async def test_s2_ec48_client_clock_in_future_clamps_to_zero(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await client.post(URL, json=_body(fake_clock, minutes_ago=-30), headers=SANDBOX_HEADERS)
    d = r.json()
    assert d["minutes_since_transfer"] == 0 and d["golden_hour_remaining_minutes"] == 60


async def test_s2_ec48_naive_timestamp_is_vietnam_time(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    # FakeClock = 2026-11-25 01:00 UTC = 08:00 giờ VN; mốc không múi giờ hiểu là giờ VN.
    r = await client.post(
        URL, json=_body(fake_clock, transferred_at="2026-11-25T07:30:00"), headers=SANDBOX_HEADERS
    )
    assert r.json()["minutes_since_transfer"] == 30


async def test_s2_ec48_report_draft_has_no_raw_account_or_injection(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Client gửi nhầm STK thô / chuỗi tiêm: máy chủ che lại, chỉ giữ [A-Za-z0-9*]."""
    r = await client.post(
        URL,
        json=_body(fake_clock, mule_masked_account=RAW_ACCOUNT),
        headers=SANDBOX_HEADERS,
    )
    draft = r.json()["police_report_draft_vi"]
    assert RAW_ACCOUNT not in draft and "1903" not in draft
    assert "***899" in draft
    assert "[Họ tên]" in draft  # định danh để người dùng tự điền, hệ thống không lưu
    r2 = await client.post(
        URL,
        json=_body(fake_clock, mule_masked_account="12**<script>\n34"),
        headers=SANDBOX_HEADERS,
    )
    assert "<script>" not in r2.json()["police_report_draft_vi"]


async def test_s2_ec48_pdf_export(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await client.post(
        URL + "?format=pdf",
        json=_body(fake_clock, mule_masked_account=RAW_ACCOUNT),
        headers=SANDBOX_HEADERS,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert r.content.rstrip().endswith(b"%%EOF")
    assert RAW_ACCOUNT.encode() not in r.content
    assert r.headers["cache-control"] == "no-store"


async def test_s2_ec48_validation_rejects_bad_bin_and_extra_fields(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    bad = await client.post(
        URL, json=_body(fake_clock, victim_bank_bin="12AB"), headers=SANDBOX_HEADERS
    )
    assert bad.status_code == 422
    extra = await client.post(URL, json=_body(fake_clock, hack="x"), headers=SANDBOX_HEADERS)
    assert extra.status_code == 422


async def test_s2_ec48_latency_under_120ms(
    triage_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    samples: list[float] = []
    for _ in range(15):
        t0 = time.perf_counter()
        r = await client.post(URL, json=_body(fake_clock), headers=SANDBOX_HEADERS)
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    assert statistics.median(samples) < 120, samples
