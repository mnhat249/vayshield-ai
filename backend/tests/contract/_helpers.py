"""Tiện ích đọc contract + so khớp cấu trúc OpenAPI (path + method + tên field)."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = REPO_ROOT / "contracts"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")


@cache
def load_openapi() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    return data


@cache
def load_json(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    return data


def _resolve(doc: dict[str, Any], ref: str) -> dict[str, Any]:
    node: Any = doc
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    result: dict[str, Any] = node
    return result


def field_shape(doc: dict[str, Any], schema: dict[str, Any], depth: int = 0) -> Any:
    """Rút gọn schema thành cây tên field (+ required) để so sánh, bỏ mô tả/ràng buộc."""
    if depth > 12:
        return "…"
    if "$ref" in schema:
        return field_shape(doc, _resolve(doc, schema["$ref"]), depth + 1)
    for combo in ("anyOf", "oneOf", "allOf"):
        if combo in schema:
            return {combo: [field_shape(doc, s, depth + 1) for s in schema[combo]]}
    if schema.get("type") == "array" and "items" in schema:
        return {"array": field_shape(doc, schema["items"], depth + 1)}
    if "properties" in schema:
        return {
            "required": sorted(schema.get("required", [])),
            "props": {
                name: field_shape(doc, sub, depth + 1)
                for name, sub in sorted(schema["properties"].items())
            },
        }
    if "enum" in schema:
        return {"enum": sorted(map(str, schema["enum"]))}
    return schema.get("type", "any")


def operation_shape(doc: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    params = sorted(
        (p["in"], p["name"].lower(), bool(p.get("required", False)))
        for p in op.get("parameters", [])
    )
    body = op.get("requestBody", {})
    body_shape = {
        ctype: field_shape(doc, media.get("schema", {}))
        for ctype, media in body.get("content", {}).items()
    }
    responses = {
        str(status): {
            ctype: field_shape(doc, media.get("schema", {}))
            for ctype, media in resp.get("content", {}).items()
        }
        for status, resp in op.get("responses", {}).items()
    }
    return {
        "operationId": op.get("operationId"),
        "params": params,
        "body_required": bool(body.get("required", False)),
        "body": body_shape,
        "responses": responses,
        "security": sorted(sorted(req) for req in op.get("security", [])),
    }


def operations(doc: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for path, item in doc.get("paths", {}).items():
        for method in HTTP_METHODS:
            if method in item:
                out[(path, method)] = item[method]
    return out


def validator_for_component(doc: dict[str, Any], name: str) -> Draft202012Validator:
    schema = {"$ref": f"#/components/schemas/{name}", "components": doc["components"]}
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def validator_for_ref(doc: dict[str, Any], schema: dict[str, Any]) -> Draft202012Validator:
    wrapped = {**schema, "components": doc["components"]}
    return Draft202012Validator(wrapped, format_checker=Draft202012Validator.FORMAT_CHECKER)


def iter_routes(routes: list[Any]) -> list[Any]:
    """Duyệt phẳng route (FastAPI mới giữ ``_IncludedRouter`` lồng nhau)."""
    out: list[Any] = []
    for route in routes:
        nested = getattr(route, "original_router", None)
        if nested is not None:
            out.extend(iter_routes(list(nested.routes)))
        else:
            out.append(route)
    return out


def validator_for_def(doc: dict[str, Any], name: str) -> Draft202012Validator:
    schema = {"$ref": f"#/$defs/{name}", "$defs": doc["$defs"]}
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
