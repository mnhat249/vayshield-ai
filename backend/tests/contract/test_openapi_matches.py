"""So khớp ``app.openapi()`` với ``contracts/openapi.yaml`` (path + method + tên field).

Lệch hợp đồng = lỗi chặn merge (CLAUDE.md §4). Test này chạy lại sau mọi task thay stub.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from app.core import contract_export
from app.core.verdict_codes import KernelSignal, VerdictCode
from tests.contract._helpers import (
    CONTRACTS,
    load_openapi,
    operation_shape,
    operations,
)

# File 4 §4.1: đủ 16 dòng endpoint (18 path) + bổ sung có căn cứ đặc tả.
SPEC_ENDPOINTS: set[tuple[str, str]] = {
    ("/api/v1/devices/register", "post"),
    ("/api/v1/policies/latest", "get"),
    ("/api/v1/audits/apk-verify", "post"),
    ("/api/v1/audits/contract-stream", "post"),
    ("/api/v1/audits/voice-terms", "post"),
    ("/api/v1/audits/{audit_id}/recalculate", "post"),
    ("/api/v1/shield/arm", "post"),
    ("/api/v1/shield/evaluate-response", "post"),
    ("/api/v1/shield/escalate", "post"),
    ("/api/v1/family/send-rescue-card", "post"),
    ("/api/v1/triage/golden-hour-dossier", "post"),
    ("/api/v1/family/pair-init", "post"),
    ("/api/v1/family/pair-confirm", "post"),
    ("/ws/v1/guardian", "get"),
    ("/api/v1/b2b/disbursement-shield", "post"),
    ("/api/v1/ops/mule-clusters", "get"),
    ("/api/v1/ops/simo-dossier", "get"),
    ("/api/v1/sandbox/simulate-event", "post"),
}
SPEC_SUPPLEMENTS: set[tuple[str, str]] = {
    ("/api/v1/devices/me", "delete"),  # File 4 §5.2 mục 4, EC-DB21
    ("/api/v1/ws-ticket", "get"),  # File 4 EC-DB12
    ("/api/v1/tts/synthesize-vi", "post"),  # team_division WP1-09
    ("/api/v1/ops/live-feed", "get"),  # File 4 §4.1 "SSE Live Threat Feed"
    ("/api/v1/ops/auth/token", "post"),  # File 6 EC-OPS15
    ("/api/v1/sandbox/sessions", "post"),  # File 4 EC-DB16 + File 6 §1.2 mục 3
    ("/healthz", "get"),  # File 6 §1.2 mục 1
}


@pytest.fixture
def app_doc(app: FastAPI) -> dict[str, Any]:
    return app.openapi()


def test_contract_is_openapi_31() -> None:
    assert load_openapi()["openapi"].startswith("3.1")


def test_contract_covers_all_spec_endpoints() -> None:
    ops = set(operations(load_openapi()))
    assert SPEC_ENDPOINTS - ops == set()
    assert SPEC_SUPPLEMENTS - ops == set()
    assert ops == SPEC_ENDPOINTS | SPEC_SUPPLEMENTS, "endpoint ngoài danh sách đã duyệt"


def test_app_paths_and_methods_match_contract(app_doc: dict[str, Any]) -> None:
    assert set(operations(app_doc)) == set(operations(load_openapi()))


def test_app_operations_fields_match_contract(app_doc: dict[str, Any]) -> None:
    contract = load_openapi()
    app_ops = operations(app_doc)
    mismatches = []
    for key, op in operations(contract).items():
        expected = operation_shape(contract, op)
        actual = operation_shape(app_doc, app_ops[key])
        if expected != actual:
            mismatches.append(key)
    assert mismatches == []


def test_operation_ids_unique() -> None:
    ids = [op["operationId"] for op in operations(load_openapi()).values()]
    assert len(ids) == len(set(ids))


def test_ec_ops23_every_operation_documents_json_500() -> None:
    """EC-OPS23: mọi operation REST khai báo 500 dạng JSON ErrorResponse (không HTML)."""
    doc = load_openapi()
    for (path, _method), op in operations(doc).items():
        if path == "/ws/v1/guardian" or op["operationId"] == "healthz":
            continue
        resp = op["responses"]["500"]
        assert list(resp["content"]) == ["application/json"], path
        assert resp["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse")
    body = doc["components"]["schemas"]["ErrorBody"]
    assert {"code", "message_vi", "trace_id"} <= set(body["required"])


def test_verdict_codes_in_contract_match_core() -> None:
    schemas = load_openapi()["components"]["schemas"]
    assert schemas["VerdictCode"]["enum"] == [c.value for c in VerdictCode]
    assert len(schemas["VerdictCode"]["enum"]) == 9
    assert schemas["KernelSignal"]["enum"] == [KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM.value]


def test_b2b_requires_partner_key_and_hmac_together() -> None:
    op = load_openapi()["paths"]["/api/v1/b2b/disbursement-shield"]["post"]
    assert op["security"] == [{"PartnerKey": [], "PartnerHmacSignature": []}]
    headers = {p["name"] for p in op["parameters"] if p["in"] == "header"}
    assert {"X-VayShield-Timestamp", "Idempotency-Key"} <= headers


def test_sse_and_ws_contract_files_match_core_models() -> None:
    """SSE/WS contract sinh thuần từ app/core (khóa) → phải khớp từng byte.

    openapi.yaml chỉ so cấu trúc (test ở trên) để task sau được sửa summary/description.
    """
    rendered = {
        contract_export.SSE_FILE: contract_export.render_json(contract_export.build_sse_schema()),
        contract_export.WS_FILE: contract_export.render_json(contract_export.build_ws_protocol()),
    }
    for name, content in rendered.items():
        current = (CONTRACTS / name).read_text(encoding="utf-8")
        assert current == content, f"{name} lệch — chạy: uv run python -m app.core.contract_export"
