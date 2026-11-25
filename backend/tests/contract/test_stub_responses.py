"""Mock M0 hợp lệ theo contract (Nhóm 2 dùng ngay qua MSW/Scalar).

Chỉ gọi route còn đánh dấu ``@stub``; khi task sau thay bằng logic thật và bỏ dấu, test của
route đó tự skip (test thật nằm trong thư mục test của task đó).
"""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from fastapi.testclient import TestClient

from app.core import mocks
from app.core.schemas.sse import CONTRACT_STREAM_PHASES
from app.core.stubs import is_stub
from tests.contract._helpers import (
    iter_routes,
    load_json,
    load_openapi,
    operations,
    validator_for_def,
    validator_for_ref,
)

NO_AUTH_OPS = {"devicesRegister", "familyPairConfirm", "opsAuthToken", "sandboxCreateSession"}
MULTIPART_OR_STREAM_OPS = {"auditsContractStream", "auditsVoiceTerms", "opsLiveFeed"}


def _route_by_operation(app: FastAPI, operation_id: str) -> APIRoute | None:
    for route in iter_routes(list(app.routes)):
        if isinstance(route, APIRoute) and route.operation_id == operation_id:
            return route
    return None


def _auth_headers(operation_id: str) -> dict[str, str]:
    if operation_id in NO_AUTH_OPS:
        return {}
    if operation_id == "familySendRescueCard":
        return {"Authorization": "Bearer gst_test_guardian_token"}
    if operation_id.startswith("ops"):
        return {"Authorization": "Bearer test.ops.jwt"}
    if operation_id == "b2bDisbursementShield":
        return {
            "X-VayShield-Partner-Key": "vsp_test_partner",
            "X-VayShield-Signature": "sha256=" + "0" * 64,
            "X-VayShield-Timestamp": "1795543200",
            "Idempotency-Key": "disb_tx_20261125_00918273",
        }
    if operation_id == "sandboxSimulateEvent":
        return {"X-Sandbox-Session": "sbx_test_session_token_0001"}
    return {
        "X-VayShield-Device-Signature": (
            "v1;device_id=019361a0-0000-7000-8000-000000000001;ts=1795543200;sig=bW9jaw"
        )
    }


def _json_operations() -> list[tuple[str, str, str]]:
    out = []
    for (path, method), op in operations(load_openapi()).items():
        op_id = op["operationId"]
        if op_id in MULTIPART_OR_STREAM_OPS or op_id in {"wsGuardian", "healthz"}:
            continue
        out.append((path, method, op_id))
    return sorted(out)


def _concrete_path(path: str) -> str:
    return path.replace("{audit_id}", str(mocks.AUDIT_ID))


def _query_for(op_id: str) -> dict[str, str]:
    if op_id == "opsSimoDossier":
        return {"mule_node_id": str(mocks.MULE_NODE_A)}
    return {}


@pytest.mark.parametrize(("path", "method", "op_id"), _json_operations())
async def test_stub_json_response_matches_contract(
    app: FastAPI, client: httpx.AsyncClient, path: str, method: str, op_id: str
) -> None:
    route = _route_by_operation(app, op_id)
    assert route is not None, op_id
    if not is_stub(route.endpoint):
        pytest.skip(f"{op_id} đã có logic thật — test thuộc task sở hữu route")
    body = mocks.SAMPLE_REQUESTS.get(op_id)
    resp = await client.request(
        method.upper(),
        _concrete_path(path),
        json=body,
        params=_query_for(op_id),
        headers=_auth_headers(op_id),
    )
    doc = load_openapi()
    op = doc["paths"][path][method]
    status = str(resp.status_code)
    assert status in op["responses"], (op_id, resp.status_code, resp.text)
    assert resp.status_code < 300, (op_id, resp.text)
    schema = op["responses"][status]["content"]["application/json"]["schema"]
    validator_for_ref(doc, schema).validate(resp.json())


@pytest.mark.parametrize(
    ("path", "method", "op_id"),
    [p for p in _json_operations() if p[2] not in NO_AUTH_OPS],
)
async def test_ec_ops23_missing_auth_returns_json_401(
    client: httpx.AsyncClient, path: str, method: str, op_id: str
) -> None:
    resp = await client.request(
        method.upper(),
        _concrete_path(path),
        json=mocks.SAMPLE_REQUESTS.get(op_id),
        params=_query_for(op_id),
    )
    assert resp.status_code == 401, op_id
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_ec_db22_contract_stream_stub_emits_5_phases_valid_sse(
    app: FastAPI, client: httpx.AsyncClient, device_headers: dict[str, str]
) -> None:
    route = _route_by_operation(app, "auditsContractStream")
    assert route is not None
    if not is_stub(route.endpoint):
        pytest.skip("contract-stream đã có logic thật (T11)")
    files = [
        ("images", ("a.webp", io.BytesIO(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "image/webp")),
        ("images", ("b.jpg", io.BytesIO(b"\xff\xd8\xff\xe0mock"), "image/jpeg")),
    ]
    data = {"metadata_json": json.dumps({"capture_source": "SCREENSHOT"})}
    resp = await client.post(
        "/api/v1/audits/contract-stream", files=files, data=data, headers=device_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-accel-buffering"] == "no"
    assert resp.headers["cache-control"] == "no-cache, no-transform"

    sse_doc = load_json("sse-events.schema.json")
    validator = validator_for_def(sse_doc, "ContractAuditStream")
    events = []
    for block in resp.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        frame = {"event": lines["event"], "data": json.loads(lines["data"])}
        validator.validate(frame)
        events.append(frame)
    names = [e["event"] for e in events if e["event"] != "ping"]
    assert names == [name for name, _ in CONTRACT_STREAM_PHASES]
    assert events[0]["data"]["image_count"] == 2


async def test_contract_stream_rejects_svg_as_json_415(
    client: httpx.AsyncClient, device_headers: dict[str, str]
) -> None:
    files = [("images", ("x.svg", io.BytesIO(b"<svg/>"), "image/svg+xml"))]
    data = {"metadata_json": json.dumps({"capture_source": "SCREENSHOT"})}
    resp = await client.post(
        "/api/v1/audits/contract-stream", files=files, data=data, headers=device_headers
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


async def test_contract_stream_rejects_more_than_4_images(
    client: httpx.AsyncClient, device_headers: dict[str, str]
) -> None:
    files = [("images", (f"{i}.jpg", io.BytesIO(b"\xff\xd8\xff"), "image/jpeg")) for i in range(5)]
    data = {"metadata_json": json.dumps({"capture_source": "SCREENSHOT"})}
    resp = await client.post(
        "/api/v1/audits/contract-stream", files=files, data=data, headers=device_headers
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_voice_terms_stub_matches_contract(
    app: FastAPI, client: httpx.AsyncClient, sandbox_headers: dict[str, str]
) -> None:
    route = _route_by_operation(app, "auditsVoiceTerms")
    assert route is not None
    if not is_stub(route.endpoint):
        pytest.skip("voice-terms đã có logic thật (T11)")
    resp = await client.post(
        "/api/v1/audits/voice-terms",
        data={"metadata_json": "{}", "transcript_text": "vay mười củ trả mười hai củ"},
        headers=sandbox_headers,
    )
    assert resp.status_code == 200, resp.text
    doc = load_openapi()
    schema = doc["paths"]["/api/v1/audits/voice-terms"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    validator_for_ref(doc, schema).validate(resp.json())


def test_guardian_ws_stub_frames_match_ws_protocol(app: FastAPI) -> None:
    ws_route = next(
        r
        for r in iter_routes(list(app.routes))
        if isinstance(r, APIWebSocketRoute) and r.path == "/ws/v1/guardian"
    )
    if not is_stub(ws_route.endpoint):
        pytest.skip("WS hub đã có logic thật (T12)")
    ws_doc = load_json("ws-protocol.json")
    server_validator = validator_for_def(ws_doc, "ServerFrame")
    with TestClient(app) as tc, tc.websocket_connect("/ws/v1/guardian?ticket=wst_test") as ws:
        hello = ws.receive_json()
        alert = ws.receive_json()
        server_validator.validate(hello)
        server_validator.validate(alert)
        assert [hello["type"], alert["type"]] == ["SERVER_HELLO", "EVENT_EMERGENCY_ALERT"]
        ack = {
            "type": "CLIENT_ALERT_ACK",
            "frame_id": "019361ab-0000-7000-8000-0000000000aa",
            "sent_at": mocks.MOCK_NOW.isoformat(),
            "payload": {"escalation_id": alert["payload"]["escalation_id"]},
        }
        validator_for_def(ws_doc, "ClientFrame").validate(ack)
        ws.send_json(ack)
        acked = ws.receive_json()
        server_validator.validate(acked)
        assert acked["type"] == "EVENT_ESCALATION_ACKNOWLEDGED"


def _frame(frame_type: str, payload: Any) -> dict[str, Any]:
    return {
        "type": frame_type,
        "frame_id": "019361ab-0000-7000-8000-0000000000bb",
        "sent_at": mocks.MOCK_NOW.isoformat(),
        "payload": payload.model_dump(mode="json"),
    }


def test_all_mock_ws_frames_valid_against_ws_protocol() -> None:
    ws_doc = load_json("ws-protocol.json")
    server_v = validator_for_def(ws_doc, "ServerFrame")
    client_v = validator_for_def(ws_doc, "ClientFrame")
    for frame_type, payload in mocks.WS_SERVER_FRAMES.items():
        server_v.validate(_frame(frame_type, payload))
    for frame_type, payload in mocks.WS_CLIENT_FRAMES.items():
        client_v.validate(_frame(frame_type, payload))
    assert set(mocks.WS_SERVER_FRAMES) == set(ws_doc["x-server-frames"])
    assert set(mocks.WS_CLIENT_FRAMES) == set(ws_doc["x-client-frames"])


def test_zero_amount_envelope_ws_alert_rejects_amount_field() -> None:
    """Payload cảnh báo người thân không được mang số tiền (Zero-Amount Privacy Envelope)."""
    ws_doc = load_json("ws-protocol.json")
    frame = _frame("EVENT_EMERGENCY_ALERT", mocks.WS_SERVER_FRAMES["EVENT_EMERGENCY_ALERT"])
    frame["payload"]["amount_vnd"] = 20_000_000
    errors = list(validator_for_def(ws_doc, "ServerFrame").iter_errors(frame))
    assert errors


def test_all_mock_sse_events_valid_against_schema() -> None:
    sse_doc = load_json("sse-events.schema.json")
    contract_v = validator_for_def(sse_doc, "ContractAuditStream")
    for name, payload in [
        *mocks.CONTRACT_STREAM_EVENTS,
        ("ping", mocks.SSE_PING),
        ("audit.error", mocks.SSE_ERROR),
    ]:
        contract_v.validate({"event": name, "data": payload.model_dump(mode="json")})
    ops_v = validator_for_def(sse_doc, "OpsLiveFeedStream")
    ops_v.validate(
        {"event": "ops.threat_signal", "data": mocks.SSE_OPS_SIGNAL.model_dump(mode="json")}
    )
    assert sse_doc["x-phase-order"] == [name for name, _ in mocks.CONTRACT_STREAM_EVENTS]
    assert sse_doc["x-transport"]["required_headers"]["X-Accel-Buffering"] == "no"
