"""POST /audits/{id}/recalculate: tất định, < 15ms, 0 LLM (File 2 §3.2, File 4 §4.1)."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import httpx

from ai_pipeline.extraction.gemini_client import reply_json
from app.core.clock import FakeClock
from tests.ai_pipeline.extraction._factory import PRIMARY, loan
from tests.api.shield._helpers import DEVICE_B, signed_post

from ._helpers import STREAM, image_part, jpeg, meta, parse_sse, signed_multipart


async def _audit(client: httpx.AsyncClient, clock: FakeClock) -> str:
    resp = await signed_multipart(
        client,
        clock,
        STREAM,
        files=[image_part(jpeg())],
        data={"metadata_json": meta()},
    )
    return str(parse_sse(resp.text)[0]["data"]["audit_id"])


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "nominal_principal_vnd": 10_000_000,
        "net_disbursed_vnd": 8_500_000,
        "total_repayment_vnd": 9_000_000,
        "tenure_days": 365,
        "edited_fields": ["net_disbursed_vnd"],
    }
    body.update(over)
    return body


async def _recalc(
    client: httpx.AsyncClient, clock: FakeClock, audit_id: str, body: dict[str, Any], **kw: Any
) -> httpx.Response:
    return await signed_post(client, clock, f"/api/v1/audits/{audit_id}/recalculate", body, **kw)


async def test_recalculate_under_15ms_and_zero_llm_calls(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """DoD: < 15ms (phần kernel, đo ở ``computed_in_ms``) và KHÔNG gọi LLM."""
    audit_id = await _audit(client, fake_clock)
    calls_before = len(env.gemini.calls)
    t0 = time.perf_counter()
    r = await _recalc(client, fake_clock, audit_id, _body())
    wall_ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["llm_calls"] == 0
    assert 0 <= data["computed_in_ms"] < 15
    assert len(env.gemini.calls) == calls_before
    assert wall_ms < 500  # cả vòng HTTP + ký + lưu in-memory vẫn nhanh (không cam kết SLA)
    assert data["audit_id"] == audit_id


async def test_user_edit_changes_verdict_deterministically(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    audit_id = await _audit(client, fake_clock)
    low = (await _recalc(client, fake_clock, audit_id, _body(net_disbursed_vnd=9_000_000))).json()
    fake_clock.advance(1)
    high = (await _recalc(client, fake_clock, audit_id, _body(net_disbursed_vnd=8_000_000))).json()
    assert low["verdict"]["apr_vn_365"] < high["verdict"]["apr_vn_365"]
    fake_clock.advance(1)
    again = (await _recalc(client, fake_clock, audit_id, _body(net_disbursed_vnd=8_000_000))).json()
    assert again["verdict"] == high["verdict"]  # tất định
    stored = env.audits.audits[UUID(audit_id)]
    assert stored.user_edited_numbers is True
    assert stored.net_disbursed_vnd == 8_000_000


async def test_recalculate_of_another_device_is_404(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    audit_id = await _audit(client, fake_clock)
    r = await _recalc(client, fake_clock, audit_id, _body(), device=DEVICE_B)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    missing = await _recalc(client, fake_clock, str(UUID(int=7)), _body())
    assert missing.status_code == 404


async def test_recalculate_requires_auth(client: httpx.AsyncClient, env: Any) -> None:
    r = await client.post(f"/api/v1/audits/{UUID(int=1)}/recalculate", json=_body())
    assert r.status_code == 401


async def test_edit_cannot_clear_advance_fee_flag(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """D2: sửa số không bao giờ gỡ cờ phí ứng trước đã phát hiện (chỉ nâng, không hạ)."""
    scam = loan(
        upfront_fee_demand={"demanded": True, "amount_vnd": 2_000_000},
        net_disbursed_vnd=0,
    )
    build_env(script={PRIMARY: [reply_json(scam)]})
    audit_id = await _audit(client, fake_clock)
    r = await _recalc(
        client,
        fake_clock,
        audit_id,
        _body(net_disbursed_vnd=9_500_000, upfront_fee_amount_vnd=0),
    )
    verdict = r.json()["verdict"]
    assert verdict["verdict_code"] == "V_RED_ADVANCE_FEE"
    assert verdict["kernel_signal"] == "INFINITE_APR_ADVANCE_FEE_SCAM"
    assert verdict["apr_vn_365"] is None


async def test_inconsistent_numbers_yield_amber_not_error(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    audit_id = await _audit(client, fake_clock)
    r = await _recalc(client, fake_clock, audit_id, _body(net_disbursed_vnd=12_000_000))
    assert r.status_code == 200
    assert r.json()["verdict"]["verdict_code"] == "V_AMBER_INCOMPLETE_DOC"
