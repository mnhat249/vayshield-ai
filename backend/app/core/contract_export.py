"""Sinh 3 file contract M0 từ Pydantic model + app FastAPI.

Dùng (trong ``backend/``)::

    uv run python -m app.core.contract_export          # ghi contracts/*
    uv run python -m app.core.contract_export --check  # chỉ so sánh, exit 1 nếu lệch

Sau M0, ``contracts/`` bị khóa: đổi contract = task riêng có người duyệt.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from app.core.config import Settings
from app.core.schemas import realtime, sse
from app.core.sse import SSE_HEADERS

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
CONTRACTS_DIR: Final = REPO_ROOT / "contracts"
OPENAPI_FILE: Final = "openapi.yaml"
SSE_FILE: Final = "sse-events.schema.json"
WS_FILE: Final = "ws-protocol.json"

_JSON_SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"


def _default(field: str) -> Any:
    """Giá trị mặc định của Settings (contract không phụ thuộc env của máy sinh)."""
    return Settings.model_fields[field].default


def _defs_for(models: list[type[BaseModel]]) -> dict[str, Any]:
    _, top = models_json_schema([(m, "validation") for m in models], ref_template="#/$defs/{model}")
    defs: dict[str, Any] = top.get("$defs", {})
    return dict(sorted(defs.items()))


def _event_variant(event: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["event", "data"],
        "properties": {
            "event": {"const": event},
            "data": {"$ref": f"#/$defs/{model.__name__}"},
        },
    }


def build_sse_schema() -> dict[str, Any]:
    contract_events = [*sse.CONTRACT_STREAM_PHASES, *sse.CONTRACT_STREAM_AUX]
    models = [m for _, m in [*contract_events, *sse.OPS_FEED_EVENTS]]
    defs = _defs_for(list(dict.fromkeys(models)))
    defs["ContractAuditStream"] = {
        "description": (
            "Một khung SSE của POST /api/v1/audits/contract-stream. 'event' = dòng 'event:', "
            "'data' = JSON 1 dòng ở dòng 'data:'."
        ),
        "oneOf": [_event_variant(name, model) for name, model in contract_events],
    }
    defs["OpsLiveFeedStream"] = {
        "description": "Một khung SSE của GET /api/v1/ops/live-feed.",
        "oneOf": [_event_variant(name, model) for name, model in sse.OPS_FEED_EVENTS],
    }
    return {
        "$schema": _JSON_SCHEMA_DIALECT,
        "$id": "vayshield/contracts/sse-events.schema.json",
        "title": "VayShield AI — Sự kiện Server-Sent Events",
        "description": (
            "Hợp đồng SSE (File 4 §4.2(A)). 5 pha phát ĐÚNG thứ tự x-phase-order; 'ping' có "
            "thể chen giữa bất kỳ lúc nào (EC-DB22, mỗi x-transport.keepalive_interval_s giây, "
            "client bỏ qua); 'audit.error' nếu có luôn là event cuối và kèm gợi ý fallback "
            "(File 2 §3.1 Tầng 4: form 3 ô → POST /api/v1/audits/{audit_id}/recalculate)."
        ),
        "x-phase-order": [name for name, _ in sse.CONTRACT_STREAM_PHASES],
        "x-transport": {
            "content_type": "text/event-stream; charset=utf-8",
            "required_headers": dict(SSE_HEADERS),
            "keepalive_event": "ping",
            "keepalive_interval_s": _default("sse_keepalive_interval_s"),
            "first_event_sla_ms": 200,
            "full_stream_sla_ms": 3500,
            "port": 443,
        },
        "oneOf": [
            {"$ref": "#/$defs/ContractAuditStream"},
            {"$ref": "#/$defs/OpsLiveFeedStream"},
        ],
        "$defs": defs,
    }


def _frame_variant(frame_type: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["type", "frame_id", "sent_at", "payload"],
        "properties": {
            "type": {"const": frame_type},
            "frame_id": {"type": "string", "format": "uuid"},
            "sent_at": {"type": "string", "format": "date-time"},
            "payload": {"$ref": f"#/$defs/{model.__name__}"},
        },
    }


def build_ws_protocol() -> dict[str, Any]:
    models: list[type[BaseModel]] = [
        *realtime.SERVER_FRAMES.values(),
        *realtime.CLIENT_FRAMES.values(),
        realtime.PushPayload,
        realtime.WsTicketResponse,
    ]
    defs = _defs_for(list(dict.fromkeys(models)))
    defs["ServerFrame"] = {
        "oneOf": [_frame_variant(t, m) for t, m in realtime.SERVER_FRAMES.items()],
    }
    defs["ClientFrame"] = {
        "oneOf": [_frame_variant(t, m) for t, m in realtime.CLIENT_FRAMES.items()],
    }
    return {
        "$schema": _JSON_SCHEMA_DIALECT,
        "$id": "vayshield/contracts/ws-protocol.json",
        "title": "VayShield AI — Giao thức WebSocket /ws/v1/guardian",
        "description": (
            "Khung tin WebSocket hai chiều người vay ↔ người thân (File 4 §4.2(C)). Mọi frame là "
            "JSON text {type, frame_id, sent_at, payload}. Payload tuân Zero-Amount Privacy "
            "Envelope: không số tiền, không tên công ty vay, không STK."
        ),
        "x-endpoint": {
            "path": "/ws/v1/guardian",
            "query": {"ticket": "vé 1 lần từ GET /api/v1/ws-ticket"},
            "ticket_ttl_s": _default("ws_ticket_ttl_s"),
            "ticket_single_use": True,
            "reconnect": "Mỗi lần reconnect phải lấy vé mới (EC-DB12)",
            "scheme": "wss khi trang chạy https (EC-OPS22), cổng 443",
        },
        "x-heartbeat": {
            "server_ping_interval_s": _default("ws_ping_interval_s"),
            "server_frame": "SERVER_PING",
            "client_reply": "CLIENT_PONG",
        },
        "x-escalation-cascade": {
            "steps": [
                "POST /api/v1/shield/escalate tạo vs:ack:timer:{escalation_id} status=PENDING",
                "Song song: EVENT_EMERGENCY_ALERT qua WS + Web Push VAPID high-urgency",
                "Người thân gửi CLIENT_ALERT_ACK trong ack_timeout_s → ACKNOWLEDGED, hủy SMS, "
                "người vay nhận EVENT_ESCALATION_ACKNOWLEDGED",
                "Quá ack_timeout_s không ACK (hoặc Push 410/404) → SMS/Email, người vay nhận "
                "EVENT_ESCALATION_FALLBACK",
            ],
            "ack_timeout_s": _default("escalation_ack_timeout_s"),
            "channels_order": ["WEBSOCKET", "WEB_PUSH", "SMS", "EMAIL"],
        },
        "x-close-codes": {str(k): v for k, v in realtime.WS_CLOSE_CODES.items()},
        "x-server-frames": list(realtime.SERVER_FRAMES),
        "x-client-frames": list(realtime.CLIENT_FRAMES),
        "oneOf": [{"$ref": "#/$defs/ServerFrame"}, {"$ref": "#/$defs/ClientFrame"}],
        "$defs": defs,
    }


def build_openapi_dict() -> dict[str, Any]:
    from app.main import create_app

    return create_app().openapi()


def render_openapi_yaml(doc: dict[str, Any]) -> str:
    import yaml

    header = (
        "# SINH TỰ ĐỘNG bởi `uv run python -m app.core.contract_export` — KHÓA ở mốc M0.\n"
        "# Đổi contract = task riêng có người duyệt (CLAUDE.md §5).\n"
    )
    body: str = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)
    return header + body


def render_json(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def render_all() -> dict[str, str]:
    return {
        OPENAPI_FILE: render_openapi_yaml(build_openapi_dict()),
        SSE_FILE: render_json(build_sse_schema()),
        WS_FILE: render_json(build_ws_protocol()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="chỉ so sánh, không ghi")
    parser.add_argument("--out", type=Path, default=CONTRACTS_DIR)
    args = parser.parse_args(argv)
    out_dir: Path = args.out
    drift: list[str] = []
    for name, content in render_all().items():
        target = out_dir / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == content:
            continue
        if args.check:
            drift.append(name)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"wrote {target}")
    if drift:
        print("contract lệch: " + ", ".join(drift), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
