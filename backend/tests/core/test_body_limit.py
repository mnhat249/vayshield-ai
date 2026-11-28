"""EC-DB05: giới hạn kích thước body ở tầng ASGI, trả 413 JSON trước khi parser đọc."""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator

import fakeredis
import httpx
import pytest

from app.core.config import AppEnv, Settings
from app.main import create_app

SANDBOX = {"X-Sandbox-Session": "sbx_test_session_token_0001"}


@pytest.fixture
def small_settings() -> Settings:
    return Settings(
        app_env=AppEnv.TEST,
        log_json=False,
        body_limit_default_bytes=2048,
        body_limit_contract_stream_bytes=8192,
        body_limit_voice_terms_bytes=16384,
    )


@pytest.fixture
async def small_client(
    small_settings: Settings, redis: fakeredis.FakeAsyncRedis
) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(small_settings), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


def _assert_413(resp: httpx.Response) -> None:
    assert resp.status_code == 413
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert resp.headers["x-trace-id"] == body["error"]["trace_id"]


async def test_ec_db05_declared_content_length_over_default_limit(
    small_client: httpx.AsyncClient,
) -> None:
    resp = await small_client.post(
        "/api/v1/sandbox/sessions", json={"vip_code": "x" * 4000}, headers=SANDBOX
    )
    _assert_413(resp)


async def test_ec_db05_chunked_body_counted_while_streaming(
    small_client: httpx.AsyncClient,
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"vip_code": "'
        for _ in range(10):
            yield b"a" * 500
        yield b'"}'

    resp = await small_client.post(
        "/api/v1/sandbox/sessions",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    _assert_413(resp)


async def test_ec_db05_contract_stream_has_its_own_larger_limit(
    small_client: httpx.AsyncClient,
) -> None:
    meta = {"metadata_json": json.dumps({"capture_source": "SCREENSHOT"})}
    ok = await small_client.post(
        "/api/v1/audits/contract-stream",
        files=[("images", ("a.jpg", io.BytesIO(b"\xff\xd8\xff" + b"0" * 4000), "image/jpeg"))],
        data=meta,
        headers=SANDBOX,
    )
    # 4KB > mặc định 2KB nhưng < 8KB của route: không bị chặn 413 (phiên Sandbox lạ → 401).
    assert ok.status_code != 413
    too_big = await small_client.post(
        "/api/v1/audits/contract-stream",
        files=[("images", ("a.jpg", io.BytesIO(b"\xff\xd8\xff" + b"0" * 9000), "image/jpeg"))],
        data=meta,
        headers=SANDBOX,
    )
    _assert_413(too_big)


async def test_ec_db05_voice_terms_limit(small_client: httpx.AsyncClient) -> None:
    resp = await small_client.post(
        "/api/v1/audits/voice-terms",
        files=[("audio", ("v.webm", io.BytesIO(b"0" * 20000), "audio/webm"))],
        data={"metadata_json": "{}"},
        headers=SANDBOX,
    )
    _assert_413(resp)


async def test_small_body_passes(small_client: httpx.AsyncClient) -> None:
    resp = await small_client.post("/api/v1/sandbox/sessions", json={})
    assert resp.status_code == 201


def test_default_limits_match_spec() -> None:
    cfg = Settings()
    assert cfg.body_limit_default_bytes == 1_048_576
    assert cfg.body_limit_contract_stream_bytes >= 4 * 1_500_000
    assert cfg.body_limit_voice_terms_bytes >= cfg.upload_max_audio_bytes
