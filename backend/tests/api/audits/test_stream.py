"""POST /audits/contract-stream: SSE 5 pha, hạ cấp, ping, bảo vệ dữ liệu (File 4 §4.2(A))."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI

from ai_pipeline.extraction.gemini_client import GeminiTransientError, reply_json
from app.core.clock import FakeClock
from app.core.schemas.audits import CaptureSource, ContractStreamMetadata
from app.core.schemas.sse import CONTRACT_STREAM_PHASES
from app.services.audit_pipeline.ingest import CpuPool, ingest_images
from app.services.audit_pipeline.stream import StreamJob, contract_events
from tests.ai_pipeline.extraction._factory import PRIMARY, PRO, SECONDARY, loan
from tests.api.shield._helpers import DEVICE_A

from ._helpers import (
    STREAM,
    blurry_image,
    image_part,
    jpeg,
    meta,
    parse_sse,
    phases,
    qr_chat_image,
    sharp_image,
    signed_multipart,
    stream_validator,
    vietqr_payload,
)

PHASE_NAMES = [name for name, _ in CONTRACT_STREAM_PHASES]


async def _post(
    client: httpx.AsyncClient, clock: FakeClock, images: list[bytes] | None = None, **meta_over: Any
) -> httpx.Response:
    files = [image_part(b, f"{i}.jpg") for i, b in enumerate(images or [jpeg()])]
    return await signed_multipart(
        client, clock, STREAM, files=files, data={"metadata_json": meta(**meta_over)}
    )


async def test_five_phases_in_order_match_sse_schema(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """DoD: thứ tự 5 pha khớp ``sse-events.schema.json`` + header chống buffering (File 6 §1.2)."""
    resp = await _post(client, fake_clock, [jpeg(), jpeg()])
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-accel-buffering"] == "no"
    assert resp.headers["cache-control"] == "no-cache, no-transform"
    frames = parse_sse(resp.text)
    validator = stream_validator()
    for frame in frames:
        validator.validate(frame)
    assert phases(frames) == PHASE_NAMES
    sanitized = frames[0]["data"]
    assert sanitized["image_count"] == 2 and sanitized["blur_ok"] is True
    verdict = next(f["data"] for f in frames if f["event"] == "audit.verdict_computed")
    assert verdict["verdict_code"] == "V_RED_CRIMINAL_USURY"  # app lạ, APR_min >= 100%
    audit_id = UUID(sanitized["audit_id"])
    assert audit_id in env.audits.audits  # đã lưu audit đã che PII


async def test_first_event_arrives_before_llm_returns(env: Any, fake_clock: FakeClock) -> None:
    """SLA first event < 200ms: ``audit.sanitized`` không chờ Gemini (LLM giả chậm 1s)."""
    from tests.ai_pipeline.extraction._factory import make_cascade

    cascade, _, _ = make_cascade({PRIMARY: [reply_json(loan(), delay_s=1.0)]}, failover=5.0)
    from ai_pipeline.extraction import ExtractionPipeline

    env.services.pipeline = ExtractionPipeline(cascade)
    pool = CpuPool(2)
    ingest = await ingest_images([jpeg()], pool)
    job = StreamJob(
        audit_id=UUID(int=1),
        principal=_principal(),
        meta=ContractStreamMetadata(capture_source=CaptureSource.SCREENSHOT),
        ingest=ingest,
        wage_vnd=320_000,
        elapsed_ms_at_start=0,
        started=0.0,
    )
    gen = contract_events(env.services, job)
    t0 = time.perf_counter()
    first = await anext(gen)
    assert (time.perf_counter() - t0) * 1000 < 200
    assert first.startswith("event: audit.sanitized")
    await gen.aclose()


def _principal() -> Any:
    from app.services.realtime.models import BorrowerPrincipal

    return BorrowerPrincipal(device_id=DEVICE_A)


async def test_ec_db22_ping_between_phases_while_llm_is_slow(
    app: FastAPI,
    build_env: Any,
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
    audit_settings: Any,
) -> None:
    """EC-DB22: ping chen giữa các pha khi chờ LLM; mọi khung (kể cả ping) khớp schema."""
    cfg = audit_settings.model_copy(update={"sse_keepalive_interval_s": 0.05})
    build_env(
        settings=cfg,
        script={PRIMARY: [reply_json(loan(), delay_s=0.4)]},
        failover=2.0,
        soft=1.0,
    )
    resp = await _post(client, fake_clock)
    frames = parse_sse(resp.text)
    validator = stream_validator()
    for frame in frames:
        validator.validate(frame)
    assert sum(1 for f in frames if f["event"] == "ping") >= 2
    assert phases(frames) == PHASE_NAMES


async def test_ec_db01_no_db_work_while_waiting_for_llm(
    app: FastAPI, build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """EC-DB01: không giữ kết nối DB suốt luồng — lưu audit chỉ xảy ra SAU khi LLM trả lời;
    route không phụ thuộc ``get_db``."""
    from app.core.db import get_db
    from app.services.audit_pipeline.repository import InMemoryAuditRepository

    timeline: list[str] = []

    class Spy(InMemoryAuditRepository):
        async def save(self, audit: Any) -> None:
            timeline.append("save")
            await super().save(audit)

        async def get(self, *a: Any) -> Any:
            timeline.append("get")
            return await super().get(*a)

    from ai_pipeline.extraction.gemini_client import GenerationRequest, GenerationResult

    async def slow(request: GenerationRequest, _key: Any) -> GenerationResult:
        timeline.append("llm_start")
        await asyncio.sleep(0.1)
        timeline.append("llm_end")
        return GenerationResult(text=loan().model_dump_json(), model=request.model)

    e = build_env(audits=Spy())
    e.gemini.handler = slow
    resp = await _post(client, fake_clock)
    assert phases(parse_sse(resp.text)) == PHASE_NAMES
    assert timeline.index("save") > timeline.index("llm_end")
    assert "get" not in timeline[: timeline.index("llm_end")]

    dependants: list[Any] = []

    def walk(dep: Any) -> None:
        dependants.append(dep.call)
        for sub in dep.dependencies:
            walk(sub)

    from tests.contract._helpers import iter_routes

    route = next(r for r in iter_routes(list(app.routes)) if getattr(r, "path", "") == STREAM)
    walk(route.dependant)
    assert get_db not in dependants


async def test_s2_ec46_soft_timeout_then_failover_to_secondary(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """S2-EC46: Gemini chính chậm quá hạn → soft-timeout rồi failover sang model dự phòng,
    luồng vẫn đủ 5 pha."""
    e = build_env(
        script={
            PRIMARY: [reply_json(loan(), delay_s=2.0)],
            SECONDARY: [reply_json(loan())],
        },
        soft=0.05,
        failover=0.2,
    )
    resp = await _post(client, fake_clock)
    assert phases(parse_sse(resp.text)) == PHASE_NAMES
    assert e.gemini.models_called() == [PRIMARY, SECONDARY]
    record = next(iter(e.audits.audits.values()))
    assert record.model_tier == SECONDARY


async def test_gemini_total_failure_falls_back_to_three_field_form(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Tầng 4 (File 2 §3.1): Gemini chết hẳn → ``audit.error`` cuối + ``MANUAL_3_FIELD_FORM``;
    ``audit_id`` ở pha 1 vẫn dùng được với /recalculate để ra phán quyết bằng 3 ô."""
    e = build_env(
        script={
            PRIMARY: [reply_json(loan(), delay_s=2.0)],
            SECONDARY: [reply_json(loan(), delay_s=2.0)],
        },
        failover=0.1,
    )
    resp = await _post(client, fake_clock)
    frames = parse_sse(resp.text)
    stream_validator()
    for frame in frames:
        stream_validator().validate(frame)
    assert frames[-1]["event"] == "audit.error"
    err = frames[-1]["data"]
    assert err["fallback"] == "MANUAL_3_FIELD_FORM" and err["retryable"] is True
    assert err["trace_id"]
    assert phases(frames)[:2] == ["audit.sanitized", "audit.vietqr_decoded"]
    assert "audit.verdict_computed" not in phases(frames)

    audit_id = frames[0]["data"]["audit_id"]
    assert e.gemini.models_called() == [PRIMARY, SECONDARY]
    body = {
        "nominal_principal_vnd": 10_000_000,
        "net_disbursed_vnd": 9_000_000,
        "total_repayment_vnd": 10_300_000,
        "tenure_days": 30,
    }
    from tests.api.shield._helpers import signed_post

    recalc = await signed_post(client, fake_clock, f"/api/v1/audits/{audit_id}/recalculate", body)
    assert recalc.status_code == 200, recalc.text
    assert recalc.json()["verdict"]["apr_vn_365"] is not None
    assert recalc.json()["llm_calls"] == 0


async def test_ec_ops23_unexpected_failure_is_audit_error_not_500(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """EC-OPS23: lỗi bất ngờ giữa luồng → ``audit.error`` JSON cuối (không HTML/stacktrace)."""

    class Exploding:
        async def run(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("secret internal detail 0909123456")

    e = build_env()
    e.services.pipeline = Exploding()
    resp = await _post(client, fake_clock)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(resp.text)
    assert frames[-1]["event"] == "audit.error"
    assert frames[-1]["data"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in resp.text and "0909123456" not in resp.text
    assert "<html" not in resp.text.lower()


async def test_s2_ec20_blurry_photo_flags_retake(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """S2-EC20: ảnh quá mờ → ``blur_ok=false`` ngay ở sự kiện đầu để client nhắc chụp lại."""
    resp = await _post(client, fake_clock, [jpeg(blurry_image())])
    frames = parse_sse(resp.text)
    assert frames[0]["data"]["blur_ok"] is False
    sharp = parse_sse((await _post(client, fake_clock, [jpeg(sharp_image())])).text)
    assert sharp[0]["data"]["blur_ok"] is True


async def test_non_loan_photo_asks_retake_without_inventing_numbers(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    irrelevant = loan(
        screen_categories=["NON_LOAN_IRRELEVANT"],
        nominal_principal_vnd=None,
        net_disbursed_vnd=None,
        total_repayment_stated_vnd=None,
        tenure_days_total=None,
        repayment_regimes=[],
        deducted_fees_at_source=[],
    )
    build_env(script={PRIMARY: [reply_json(irrelevant)]})
    frames = parse_sse((await _post(client, fake_clock)).text)
    assert frames[-1]["event"] == "audit.error"
    assert frames[-1]["data"]["fallback"] == "RETAKE_PHOTO"
    assert "audit.fields_extracted" not in phases(frames)


async def test_vietqr_found_masked_and_server_reports_mule(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """VietQR của đối phương: STK che (``1903****8899``); báo mule với evidence channel + vector
    do SERVER tạo; client không có đường nào tự khai các trường đó."""
    from app.mule_graph.repository import InMemoryRepository
    from app.mule_graph.service import EMBEDDING_DIM, MuleGraphService
    from app.security.blind_index import BlindIndexer
    from app.security.envelope import EnvelopeCipher
    from app.services.audit_pipeline.ports import MuleGraphPort
    from tests.security._helpers import KEK_A, make_settings

    indexer = BlindIndexer.from_settings(make_settings())
    repo = InMemoryRepository()
    service = MuleGraphService(repo, indexer, EnvelopeCipher(KEK_A))
    spoof = loan(
        claimed_lender_name="Home Credit",
        upfront_fee_demand={
            "demanded": True,
            "fee_name_claimed": "Phí mở băng",
            "amount_vnd": 2_000_000,
            "beneficiary_account_holder": "NGUYEN VAN B",
        },
        net_disbursed_vnd=0,
    )
    build_env(
        script={PRIMARY: [reply_json(spoof)]},
        mule=MuleGraphPort(service, repo, indexer),
    )
    qr_img = jpeg(qr_chat_image(vietqr_payload("1903123498899")))
    frames = parse_sse((await _post(client, fake_clock, [qr_img])).text)
    qr = next(f["data"] for f in frames if f["event"] == "audit.vietqr_decoded")
    assert qr["found"] is True and qr["bank_bin"] == "970407"
    assert qr["masked_account"] == "1903****8899"
    assert qr["preset_amount_vnd"] == 2_000_000
    assert "1903123498899" not in json.dumps(frames)
    verdict = next(f["data"] for f in frames if f["event"] == "audit.verdict_computed")
    assert verdict["verdict_code"] in {"V_RED_ADVANCE_FEE", "V_RED_BRAND_SPOOF"}
    assert verdict["kernel_signal"] == "INFINITE_APR_ADVANCE_FEE_SCAM"
    assert verdict["official_hotline_verify"] == "1900 0001"
    nodes = list(repo.nodes.values())
    assert len(nodes) == 1 and nodes[0].masked_account_display.endswith("899")
    edge = repo.edges[0]
    assert edge.evidence_channel.value == "VIETQR_CRYPTO_DECODED"
    assert edge.scam_script_vec is not None and len(edge.scam_script_vec) == EMBEDDING_DIM


async def test_client_cannot_inject_evidence_channel_or_script_vector(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    for field in ("evidence_channel", "scam_script_vec"):
        resp = await _post(client, fake_clock, None, **{field: "VIETQR_CRYPTO_DECODED"})
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_ambiguous_extraction_is_not_guessed(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Số liệu mơ hồ (độ tin cậy thấp → Conformal ép xác nhận): APR "không xác định" →
    AMBER + mã NEEDS_USER_CONFIRMATION, không đoán lãi suất."""
    shaky = loan(critical_fields_min_confidence=0.3)
    build_env(
        script={PRIMARY: [reply_json(shaky)], PRO: [reply_json(shaky)]},
    )
    frames = parse_sse((await _post(client, fake_clock)).text)
    verdict = next(f["data"] for f in frames if f["event"] == "audit.verdict_computed")[
        "verdict_code"
    ]
    data = next(f["data"] for f in frames if f["event"] == "audit.verdict_computed")
    assert verdict == "V_AMBER_INCOMPLETE_DOC"
    assert data["apr_vn_365"] is None
    assert "NEEDS_USER_CONFIRMATION" in data["contradictions"]


async def test_ec_db26_null_bytes_removed_from_output_and_storage(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    dirty = loan(claimed_lender_name="App\x00 Vay\x07 Nhanh")
    e = build_env(script={PRIMARY: [reply_json(dirty)]})
    resp = await _post(client, fake_clock)
    assert "\\u0000" not in resp.text and "\x00" not in resp.text
    record = next(iter(e.audits.audits.values()))
    assert "\x00" not in json.dumps(record.context, ensure_ascii=False)
    assert "\x07" not in json.dumps(record.context, ensure_ascii=False)


async def test_stored_context_is_masked_and_shallow(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Lưu audit đã che PII: không giữ STK/tên chủ TK/SĐT; JSON đủ nông (EC-DB18)."""
    from app.db_schema.sanitize import json_depth

    leaky = loan(
        extraction_notes_vi=["Gọi 0909123456 gửi OTP 482913 vào tài khoản 1903123498899"],
        upfront_fee_demand={
            "demanded": True,
            "amount_vnd": 1_000_000,
            "beneficiary_account_number": "1903123498899",
            "beneficiary_account_holder": "NGUYEN VAN B",
        },
    )
    e = build_env(script={PRIMARY: [reply_json(leaky)]})
    await _post(client, fake_clock)
    record = next(iter(e.audits.audits.values()))
    blob = json.dumps(record.context, ensure_ascii=False)
    for secret in ("0909123456", "482913", "1903123498899", "NGUYEN VAN B"):
        assert secret not in blob
    assert json_depth(record.context) <= 5
    assert record.context["prov"]["personal"] is True


async def test_pipeline_not_configured_degrades_to_manual_form(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    build_env(no_pipeline=True)
    frames = parse_sse((await _post(client, fake_clock)).text)
    assert frames[-1]["event"] == "audit.error"
    assert frames[-1]["data"]["fallback"] == "MANUAL_3_FIELD_FORM"


async def test_secondary_transient_errors_do_not_leak_to_client(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    from ai_pipeline.extraction.gemini_client import FakeReply

    build_env(
        script={
            PRIMARY: [FakeReply(error=GeminiTransientError("upstream 503 key=AIza-xyz"))] * 2,
            SECONDARY: [FakeReply(error=GeminiTransientError("upstream 503 key=AIza-xyz"))] * 2,
        }
    )
    resp = await _post(client, fake_clock)
    assert "AIza" not in resp.text
    assert parse_sse(resp.text)[-1]["event"] == "audit.error"
