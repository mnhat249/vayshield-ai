"""Tiện ích OpenAPI: khai báo response lỗi chuẩn + bổ sung mô tả WebSocket vào schema.

FastAPI không đưa route WebSocket vào OpenAPI, nên ``/ws/v1/guardian`` được mô tả thủ công
(GET + 101 Switching Protocols, ``x-websocket-protocol`` trỏ tới ``contracts/ws-protocol.json``).
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema

from app.core.errors import ErrorResponse
from app.core.schemas.audits import ContractStreamMetadata, VoiceTermsMetadata

_ERROR_DESCRIPTIONS: Final[dict[int, str]] = {
    304: "Không đổi (ETag khớp)",
    400: "Yêu cầu hỏng",
    401: "Thiếu/sai xác thực",
    403: "Không đủ quyền",
    404: "Không tìm thấy",
    409: "Xung đột trạng thái / Idempotency",
    413: "Payload quá lớn",
    415: "Định dạng không hỗ trợ",
    422: "Dữ liệu không hợp lệ",
    423: "Đã khóa (quá số lần thử)",
    429: "Vượt giới hạn tần suất / ngân sách",
    500: "Lỗi nội bộ (JSON, không bao giờ HTML — EC-OPS23)",
    503: "Dịch vụ hạ cấp",
}

WS_GUARDIAN_PATH: Final = "/ws/v1/guardian"


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Responses cho decorator route. Luôn kèm 422 + 429 + 500."""
    codes = sorted({*status_codes, 422, 429, 500})
    out: dict[int | str, dict[str, Any]] = {}
    for code in codes:
        if code == 304:
            out[code] = {"description": _ERROR_DESCRIPTIONS[code]}
        else:
            out[code] = {"model": ErrorResponse, "description": _ERROR_DESCRIPTIONS[code]}
    return out


def _ws_path_item() -> dict[str, Any]:
    return {
        "get": {
            "tags": ["realtime"],
            "summary": "WebSocket hub người vay ↔ người thân (nâng cấp 101)",
            "description": (
                "Kết nối WebSocket hai chiều. Lấy vé bằng GET /api/v1/ws-ticket (TTL 45s, dùng 1 "
                "lần). Ping 20s, ACK bắt buộc cho EVENT_EMERGENCY_ALERT trong 10s. Khung tin: "
                "contracts/ws-protocol.json."
            ),
            "operationId": "wsGuardian",
            "parameters": [
                {
                    "name": "ticket",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string", "minLength": 16, "maxLength": 128},
                    "description": "Vé một lần từ /api/v1/ws-ticket",
                }
            ],
            "responses": {
                "101": {"description": "Switching Protocols — đã nâng cấp WebSocket"},
                "403": {"description": "Vé không hợp lệ (đóng với mã 4401 sau khi accept)"},
            },
            "x-websocket-protocol": "contracts/ws-protocol.json",
        }
    }


def build_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version="3.1.0",
        summary=app.summary,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        servers=app.servers,
    )
    schema.setdefault("paths", {})[WS_GUARDIAN_PATH] = _ws_path_item()
    _merge_partner_security(schema)
    _add_extra_components(schema)
    app.openapi_schema = schema
    return schema


def _merge_partner_security(schema: dict[str, Any]) -> None:
    """FastAPI sinh mỗi Security dependency thành 1 phần tử (nghĩa OR). B2B cần CẢ khóa đối tác
    VÀ chữ ký HMAC → gộp thành 1 requirement (nghĩa AND)."""
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            security = operation.get("security") if isinstance(operation, dict) else None
            if not security:
                continue
            names = {name for req in security for name in req}
            if {"PartnerKey", "PartnerHmacSignature"} <= names:
                operation["security"] = [{"PartnerKey": [], "PartnerHmacSignature": []}]


def _add_extra_components(schema: dict[str, Any]) -> None:
    """Model dùng trong trường multipart ``metadata_json`` (chuỗi JSON) — đưa vào components để
    client sinh type."""

    _, top = models_json_schema(
        [(ContractStreamMetadata, "validation"), (VoiceTermsMetadata, "validation")],
        ref_template="#/components/schemas/{model}",
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in top.get("$defs", {}).items():
        components.setdefault(name, definition)
    schema["components"]["schemas"] = dict(sorted(components.items()))


def install_openapi(app: FastAPI) -> None:
    def _openapi() -> dict[str, Any]:
        return build_openapi(app)

    app.openapi = _openapi  # type: ignore[method-assign]
