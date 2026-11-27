"""API Ops: cụm mule, hồ sơ SIMO (không PII, unseal ghi chuỗi băm), SSE live feed."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from app.copilot.advisory import OFFICIAL_HOTLINE_VAR
from app.copilot.feed import publish_threat_signal, threat_signal_frames
from app.copilot.repository import InMemoryOpsRepository
from app.core.clock import FakeClock
from app.core.schemas.sse import OpsThreatSignal
from app.db_schema.hashchain import GENESIS_HASH, compute_entry_hash
from app.security.tickets import TicketNamespace, consume_ticket

from .conftest import ACCOUNT_A, ACCOUNT_B, ANALYST_USER, Seeded

CLUSTERS = "/api/v1/ops/mule-clusters"
DOSSIER = "/api/v1/ops/simo-dossier"


async def test_clusters_list_with_nodes_links_and_masking(
    ops_app: FastAPI, client: httpx.AsyncClient, analyst_headers: dict[str, str], seeded: Seeded
) -> None:
    r = await client.get(CLUSTERS, headers=analyst_headers)
    assert r.status_code == 200
    page = r.json()
    assert page["next_cursor"] is None
    (cluster,) = page["items"]
    assert cluster["cluster_id"] == str(seeded.cluster_id)
    assert cluster["severity_level"] == "CRITICAL_WAVE"
    assert cluster["impersonated_lender"] == "Home Credit"
    assert cluster["is_simulated_seed"] is False
    assert {n["masked_account_display"] for n in cluster["nodes"]} == {"***899", "***111"}
    assert all(n["is_dynamic_va_range"] for n in cluster["nodes"])
    assert {link["relation"] for link in cluster["links"]} == {
        "SAME_VA_PREFIX",
        "SHARED_REPORTER_WAVE",
        "SHARED_SCAM_SCRIPT",
    }
    text = r.text
    assert ACCOUNT_A not in text and ACCOUNT_B not in text
    assert not any(str(d) in text for d in seeded.devices)  # không lộ định danh thiết bị báo cáo


async def test_clusters_severity_filter_and_pagination(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    seeded: Seeded,
    ops_repo: InMemoryOpsRepository,
    fake_clock: FakeClock,
) -> None:
    import dataclasses
    from datetime import timedelta

    src = ops_repo.graph.clusters[seeded.cluster_id]
    extra_ids = []
    for i in range(2):
        cid = uuid4()
        extra_ids.append(cid)
        ops_repo.graph.clusters[cid] = dataclasses.replace(
            src,
            id=cid,
            campaign_label="[Dữ liệu Mô phỏng Thực tiễn] Chiến dịch mẫu",
            severity_level=src.severity_level.__class__.WATCH,
            last_seen_at=fake_clock.now() - timedelta(hours=i + 1),
        )
    watch = await client.get(CLUSTERS, params={"severity": "WATCH"}, headers=analyst_headers)
    assert len(watch.json()["items"]) == 2
    assert all(c["is_simulated_seed"] for c in watch.json()["items"])  # EC-OPS16

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, str | int] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(CLUSTERS, params=params, headers=analyst_headers)).json()
        seen += [c["cluster_id"] for c in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3 and len(set(seen)) == 3
    assert seen[0] == str(seeded.cluster_id)  # mới nhất trước


async def test_clusters_bad_cursor_and_limit_are_422(
    ops_app: FastAPI, client: httpx.AsyncClient, analyst_headers: dict[str, str]
) -> None:
    assert (
        await client.get(CLUSTERS, params={"cursor": "!!!not-base64"}, headers=analyst_headers)
    ).status_code == 422
    assert (
        await client.get(CLUSTERS, params={"limit": 0}, headers=analyst_headers)
    ).status_code == 422
    assert (
        await client.get(CLUSTERS, params={"severity": "BOGUS"}, headers=analyst_headers)
    ).status_code == 422


async def test_dossier_contains_no_pii_and_no_raw_account(
    ops_app: FastAPI, client: httpx.AsyncClient, analyst_headers: dict[str, str], seeded: Seeded
) -> None:
    """DoD: hồ sơ SIMO (chưa unseal) không chứa STK thô, định danh thiết bị hay PII nạn nhân."""
    r = await client.get(
        DOSSIER, params={"mule_node_id": str(seeded.node_a)}, headers=analyst_headers
    )
    assert r.status_code == 200
    d = r.json()
    assert d["masked_account_display"] == "***899"
    assert d["unsealed_account_number"] is None
    assert d["audit_chain_entry_hash"] is None
    assert d["account_holder_masked"] is None
    assert d["distinct_device_quorum"] == 2
    assert d["cluster_label"].startswith("VA-RANGE")
    assert {e["channel"] for e in d["evidence"]} == {
        "VIETQR_CRYPTO_DECODED",
        "OCR_RECEIPT_EXTRACTED",
    }
    for e in d["evidence"]:
        assert "***899" in e["masked_snippet_vi"]
    body = r.text
    assert ACCOUNT_A not in body and ACCOUNT_A[:8] not in body and ACCOUNT_A[-4:] not in body
    assert not any(str(dev) in body for dev in seeded.devices)
    assert "reporter" not in body
    assert r.headers["cache-control"] == "no-store"
    # Bản nháp thông báo: sạch URL/SĐT, chỉ có biến mẫu hotline chính thức.
    assert OFFICIAL_HOTLINE_VAR in d["advisory_draft_vi"]
    assert "http" not in d["advisory_draft_vi"]


async def test_dossier_unknown_node_404_and_bad_uuid_422(
    ops_app: FastAPI, client: httpx.AsyncClient, analyst_headers: dict[str, str]
) -> None:
    r = await client.get(DOSSIER, params={"mule_node_id": str(uuid4())}, headers=analyst_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    r2 = await client.get(DOSSIER, params={"mule_node_id": "nope"}, headers=analyst_headers)
    assert r2.status_code == 422


async def test_unseal_writes_audit_chain_before_returning_account(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    seeded: Seeded,
    ops_repo: InMemoryOpsRepository,
    fake_clock: FakeClock,
) -> None:
    r = await client.get(
        DOSSIER,
        params={"mule_node_id": str(seeded.node_a), "unseal": "true"},
        headers=analyst_headers,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["unsealed_account_number"] == ACCOUNT_A
    assert r.headers["cache-control"] == "no-store"
    (entry,) = ops_repo.audit_entries
    actor, action, target, at, digest = entry
    assert (actor, action, target) == (ANALYST_USER, "SIMO_DOSSIER_UNSEAL", seeded.node_a)
    assert at == fake_clock.now()
    assert (
        d["audit_chain_entry_hash"]
        == digest
        == compute_entry_hash(GENESIS_HASH, actor, action, target, at)
    )
    # Mục thứ hai nối đúng vào mục trước (hash-chain).
    await client.get(
        DOSSIER,
        params={"mule_node_id": str(seeded.node_b), "unseal": "true"},
        headers=analyst_headers,
    )
    assert ops_repo.audit_entries[1][4] == compute_entry_hash(
        digest, ANALYST_USER, "SIMO_DOSSIER_UNSEAL", seeded.node_b, fake_clock.now()
    )


async def test_unseal_fails_closed_when_audit_chain_unavailable(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    seeded: Seeded,
    ops_repo: InMemoryOpsRepository,
) -> None:
    ops_repo.fail_audit = True
    r = await client.get(
        DOSSIER,
        params={"mule_node_id": str(seeded.node_a), "unseal": "true"},
        headers=analyst_headers,
    )
    assert r.status_code == 503
    assert ACCOUNT_A not in r.text and ACCOUNT_A[-6:] not in r.text


async def test_unseal_denied_for_non_analyst_and_leaves_no_audit(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    viewer_headers: dict[str, str],
    seeded: Seeded,
    ops_repo: InMemoryOpsRepository,
) -> None:
    r = await client.get(
        DOSSIER,
        params={"mule_node_id": str(seeded.node_a), "unseal": "true"},
        headers=viewer_headers,
    )
    assert r.status_code == 403
    assert ops_repo.audit_entries == []


async def test_unseal_without_kek_returns_503(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    seeded: Seeded,
    ops_repo: InMemoryOpsRepository,
) -> None:
    from app.copilot.deps import get_ops_cipher

    ops_app.dependency_overrides[get_ops_cipher] = lambda: None
    r = await client.get(
        DOSSIER,
        params={"mule_node_id": str(seeded.node_a), "unseal": "true"},
        headers=analyst_headers,
    )
    assert r.status_code == 503
    assert ops_repo.audit_entries == []


async def test_dossier_status_matches_quorum_rule(
    ops_app: FastAPI, client: httpx.AsyncClient, analyst_headers: dict[str, str], seeded: Seeded
) -> None:
    for node in (seeded.node_a, seeded.node_b):
        d = (
            await client.get(DOSSIER, params={"mule_node_id": str(node)}, headers=analyst_headers)
        ).json()
        assert d["simo_dossier_status"] == "QUORUM_REACHED"
        assert d["distinct_device_quorum"] >= 2


# --- SSE live feed ---------------------------------------------------------------------------


async def test_stream_ticket_is_single_use_and_bound_to_role(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    redis: Redis,
) -> None:
    r = await client.get("/api/v1/ops/stream-ticket", headers=analyst_headers)
    assert r.status_code == 200
    t = r.json()
    assert t["ttl_seconds"] == 45
    assert t["stream_path"] == f"/api/v1/ops/live-feed?ticket={t['ticket']}"
    claims = await consume_ticket(redis, t["ticket"], namespace=TicketNamespace.OPS_STREAM)
    assert claims.role == "FRAUD_ANALYST" and claims.subject == ANALYST_USER
    second = await client.get(f"/api/v1/ops/live-feed?ticket={t['ticket']}")
    assert second.status_code == 401
    assert second.headers["content-type"].startswith("application/json")


async def test_live_feed_rejects_bad_ticket_with_json_401(
    ops_app: FastAPI, client: httpx.AsyncClient
) -> None:
    r = await client.get("/api/v1/ops/live-feed", params={"ticket": "x" * 43})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "TICKET_INVALID_OR_CONSUMED"


async def test_live_feed_http_stream_consumes_ticket_and_sets_sse_headers(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    monkeypatch: object,
) -> None:
    import app.api.routes.ops as ops_routes

    monkeypatch.setattr(  # type: ignore[attr-defined]
        ops_routes, "threat_signal_frames", partial(threat_signal_frames, max_seconds=0.3)
    )
    t = (await client.get("/api/v1/ops/stream-ticket", headers=analyst_headers)).json()
    r = await client.get(t["stream_path"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no"
    assert (await client.get(t["stream_path"])).status_code == 401  # vé dùng 1 lần


def _signal() -> OpsThreatSignal:
    return OpsThreatSignal(
        signal_id=UUID("019361a9-0000-7000-8000-000000000009"),
        kind="QUORUM_REACHED",
        cluster_id=None,
        mule_node_id=None,
        bank_bin="970407",
        masked_account_display="***899",
        severity_level="CRITICAL_WAVE",
        occurred_at=__import__("datetime").datetime(
            2026, 11, 25, 1, 6, 40, tzinfo=__import__("datetime").UTC
        ),
        summary_vi="Tài khoản ***899 đạt quorum 2 thiết bị độc lập",
    )


async def test_live_feed_frames_follow_contract_event_shape(redis: Redis) -> None:
    frames: list[str] = []

    async def collect() -> None:
        async for f in threat_signal_frames(redis, max_seconds=5, max_events=1):
            frames.append(f)

    task = asyncio.create_task(collect())
    for _ in range(50):  # đợi subscriber đăng ký
        await asyncio.sleep(0.02)
        if await publish_threat_signal(redis, _signal()):
            break
    await asyncio.wait_for(task, timeout=5)
    (frame,) = frames
    lines = frame.strip().split("\n")
    assert lines[0] == "id: 019361a9-0000-7000-8000-000000000009"
    assert lines[1] == "event: ops.threat_signal"
    payload = json.loads(lines[2].removeprefix("data: "))
    assert payload["masked_account_display"] == "***899"
    assert payload["kind"] == "QUORUM_REACHED"


async def test_live_feed_skips_corrupt_messages(redis: Redis) -> None:
    frames: list[str] = []

    async def collect() -> None:
        async for f in threat_signal_frames(redis, max_seconds=5, max_events=1):
            frames.append(f)

    task = asyncio.create_task(collect())
    for _ in range(50):
        await asyncio.sleep(0.02)
        if await redis.publish("vs:ops:threat-signals", "{not json"):
            break
    await publish_threat_signal(redis, _signal())
    await asyncio.wait_for(task, timeout=5)
    assert len(frames) == 1 and "ops.threat_signal" in frames[0]
