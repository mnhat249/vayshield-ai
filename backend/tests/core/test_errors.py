"""EC-OPS23: không bao giờ trả HTML/stacktrace; luôn JSON {error:{code,message_vi,trace_id}}."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.main import create_app


class _Body(BaseModel):
    phone: str
    amount_vnd: int


def _app_with_failing_routes(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("bí mật nội bộ C:\\srv\\vayshield\\secret.py dòng 42")

    @app.get("/app-error")
    async def app_error() -> None:
        raise AppError(ErrorCode.RATE_LIMITED, 429, headers={"Retry-After": "30"})

    @app.post("/echo")
    async def echo(body: _Body) -> dict[str, int]:
        return {"amount_vnd": body.amount_vnd}

    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _assert_json_error(resp: httpx.Response, status: int, code: str) -> dict[str, object]:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message_vi"]
    assert len(body["error"]["trace_id"]) == 32
    assert resp.headers["x-trace-id"] == body["error"]["trace_id"]
    return dict(body["error"])


async def test_ec_ops23_unhandled_exception_is_json_500_without_traceback(
    settings: Settings,
) -> None:
    async with await _client(_app_with_failing_routes(settings)) as client:
        resp = await client.get("/boom")
    _assert_json_error(resp, 500, "INTERNAL_ERROR")
    assert "Traceback" not in resp.text
    assert "secret.py" not in resp.text
    assert "<html" not in resp.text.lower()


async def test_ec_ops23_unknown_route_is_json_404(client: httpx.AsyncClient) -> None:
    resp = await client.get("/khong-ton-tai")
    _assert_json_error(resp, 404, "NOT_FOUND")


async def test_ec_ops23_wrong_method_is_json_405(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/shield/arm")
    _assert_json_error(resp, 405, "METHOD_NOT_ALLOWED")


async def test_ec_ops23_validation_error_does_not_echo_input(settings: Settings) -> None:
    async with await _client(_app_with_failing_routes(settings)) as client:
        resp = await client.post("/echo", json={"phone": "0909123456", "amount_vnd": "nhiều"})
    err = _assert_json_error(resp, 422, "VALIDATION_ERROR")
    assert "0909123456" not in resp.text
    assert "nhiều" not in resp.text
    assert err["details"] == [{"field": "body.amount_vnd", "issue": "int_parsing"}]


async def test_ec_ops23_malformed_json_body_is_json_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/devices/register",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    _assert_json_error(resp, 422, "VALIDATION_ERROR")


async def test_app_error_keeps_status_code_and_headers(settings: Settings) -> None:
    async with await _client(_app_with_failing_routes(settings)) as client:
        resp = await client.get("/app-error")
    _assert_json_error(resp, 429, "RATE_LIMITED")
    assert resp.headers["retry-after"] == "30"
