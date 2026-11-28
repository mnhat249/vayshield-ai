"""Review T11: side-effect tách khỏi kết nối, nhiều khoản vay không làm mất cờ đỏ."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import fakeredis
import httpx

from ai_pipeline.extraction.gemini_client import reply_json
from app.core.clock import FakeClock
from app.core.schemas.audits import CaptureSource, ContractStreamMetadata
from app.core.schemas.common import HsmState
from app.services.audit_pipeline.ingest import CpuPool, ingest_images
from app.services.audit_pipeline.stream import _BACKGROUND, StreamJob, contract_events
from app.services.realtime.models import BorrowerPrincipal
from app.shield_state.store import ShieldStateStore
from tests.ai_pipeline.extraction._factory import PRIMARY, loan
from tests.api.shield._helpers import DEVICE_A

from ._helpers import STREAM, image_part, jpeg, meta, parse_sse, phases, signed_multipart

TWO_BRANDS = {"distinct_loan_brands_detected": ["App A", "App B"]}


async def _job(env: Any, n_images: int = 1) -> StreamJob:
    ingest = await ingest_images([jpeg()] * n_images, CpuPool(2))
    return StreamJob(
        audit_id=UUID(int=99),
        principal=BorrowerPrincipal(device_id=DEVICE_A),
        meta=ContractStreamMetadata(capture_source=CaptureSource.SCREENSHOT),
        ingest=ingest,
        wage_vnd=320_000,
        elapsed_ms_at_start=0,
        started=0.0,
    )


async def test_client_disconnect_after_verdict_still_saves_audit_and_hard_stops(
    build_env: Any, fake_clock: FakeClock, redis: fakeredis.FakeAsyncRedis
) -> None:
    """MAJOR: client ngắt ngay sau verdict_computed (RAG còn chậm) — audit vẫn được lưu,
    HSM vẫn hard-stop, /recalculate không 404."""
    scam = loan(upfront_fee_demand={"demanded": True, "amount_vnd": 2_000_000}, net_disbursed_vnd=0)
    e = build_env(script={PRIMARY: [reply_json(scam)]})

    async def slow(code: str) -> tuple[Any, ...]:
        await asyncio.sleep(0.15)
        return ()

    e.services.legal.cited_clauses = slow
    job = await _job(e)
    gen = contract_events(e.services, job)
    seen: list[str] = []
    async for frame in gen:
        seen.append(frame.split("\n", 1)[0])
        if "audit.verdict_computed" in frame:
            break
    await gen.aclose()  # client ngắt
    assert seen[-1] == "event: audit.verdict_computed"
    await asyncio.gather(*list(_BACKGROUND))
    assert job.audit_id in e.audits.audits
    snap = await ShieldStateStore(redis).load(
        str(DEVICE_A), int(fake_clock.now().timestamp() * 1000)
    )
    assert HsmState.S3_ADVANCE_FEE_HARD_STOP in {f.state for f in snap.fibers}


async def test_multiple_loans_with_upfront_fee_keep_red_flag(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """MAJOR 3: 2 thương hiệu + phí ứng trước → vẫn ra đỏ, không gộp thành lỗi RETAKE_PHOTO."""
    scam = loan(
        claimed_lender_name="App A",
        upfront_fee_demand={"demanded": True, "amount_vnd": 2_000_000},
        net_disbursed_vnd=0,
        **TWO_BRANDS,
    )
    other = loan(claimed_lender_name="App B", distinct_loan_brands_detected=["App B"])
    first = loan(
        claimed_lender_name="App A",
        distinct_loan_brands_detected=["App A"],
        upfront_fee_demand={"demanded": True, "amount_vnd": 2_000_000},
        net_disbursed_vnd=0,
    )
    e = build_env(
        script={PRIMARY: [reply_json(scam), reply_json(first), reply_json(other)]},
    )
    resp = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(jpeg(), "a.jpg"), image_part(jpeg(), "b.jpg")],
        data={"metadata_json": meta()},
    )
    frames = parse_sse(resp.text)
    assert phases(frames)[-1] == "audit.legal_explained", [f["event"] for f in frames]
    verdict = next(f["data"] for f in frames if f["event"] == "audit.verdict_computed")
    assert verdict["verdict_code"] == "V_RED_ADVANCE_FEE"
    assert verdict["apr_vn_365"] is None
    assert len(e.audits.audits) == 1


async def test_multiple_loans_without_red_signal_is_amber_not_error(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    a = loan(claimed_lender_name="App A", distinct_loan_brands_detected=["App A"])
    b = loan(claimed_lender_name="App B", distinct_loan_brands_detected=["App B"])
    merged = loan(**TWO_BRANDS)
    build_env(script={PRIMARY: [reply_json(merged), reply_json(a), reply_json(b)]})
    resp = await signed_multipart(
        client,
        fake_clock,
        STREAM,
        files=[image_part(jpeg(), "a.jpg"), image_part(jpeg(), "b.jpg")],
        data={"metadata_json": meta()},
    )
    frames = parse_sse(resp.text)
    assert phases(frames)[-1] == "audit.legal_explained", [f["event"] for f in frames]
    verdict = next(f["data"] for f in frames if f["event"] == "audit.verdict_computed")
    assert verdict["verdict_code"] == "V_AMBER_INCOMPLETE_DOC"
    assert "MULTIPLE_LOANS_MIXED" in verdict["contradictions"]


async def test_sandbox_principal_records_sandbox_origin(
    build_env: Any, fake_clock: FakeClock
) -> None:
    e = build_env()
    job = await _job(e)
    object.__setattr__(
        job, "principal", BorrowerPrincipal(device_id=DEVICE_A, sandbox_namespace="sbx_1")
    )
    async for _ in contract_events(e.services, job):
        pass
    record = e.audits.audits[job.audit_id]
    assert record.trigger_origin.value == "SANDBOX_SIM"
    assert record.sandbox_namespace == "sbx_1"
