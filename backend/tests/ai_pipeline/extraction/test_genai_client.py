"""Adapter ``GoogleGenAIClient``: ánh xạ lỗi mạng (review major) — KHÔNG gọi mạng thật."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ai_pipeline.extraction import genai_client
from ai_pipeline.extraction.gemini_client import (
    GeminiAuthError,
    GeminiBadRequestError,
    GeminiQuotaError,
    GeminiTransientError,
    GenerationRequest,
)
from ai_pipeline.extraction.schemas import MultiImageLoanExtraction

REQ = GenerationRequest(
    model="flash", system_instruction="x", parts=(), response_schema=MultiImageLoanExtraction
)


def _install(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    class _Models:
        async def generate_content(self, **_: Any) -> Any:
            raise exc

    class _Aio:
        models = _Models()

    class _Client:
        def __init__(self, **_: Any) -> None:
            self.aio = _Aio()

    monkeypatch.setattr(genai_client, "genai", SimpleNamespace(Client=_Client))


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("đứt cáp"),
        httpx.ReadTimeout("chậm"),
        httpx.RemoteProtocolError("ngắt"),
        TimeoutError(),
        ConnectionResetError(),
    ],
)
async def test_s2_ec46_network_errors_become_transient(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    """S2-EC46: httpx.ConnectError/ReadTimeout không phải OSError — vẫn phải thành lỗi tạm thời
    để cascade xoay key / chuyển model thay vì lọt ra ngoài."""
    _install(monkeypatch, exc)
    with pytest.raises(GeminiTransientError):
        await genai_client.GoogleGenAIClient().generate(REQ, api_key=SecretStr("k"))


@pytest.mark.parametrize(
    ("code", "err"),
    [
        (429, GeminiQuotaError),
        (401, GeminiAuthError),
        (403, GeminiAuthError),
        (400, GeminiBadRequestError),
        (503, GeminiTransientError),
        (None, GeminiTransientError),
    ],
)
def test_map_api_error(code: int | None, err: type[Exception]) -> None:
    assert isinstance(genai_client.map_api_error(code), err)
