"""Tiện ích SSE: định dạng khung + keepalive ping (EC-DB22)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from app.core.schemas.sse import Ping
from app.core.sse import SSE_HEADERS, format_sse, with_keepalive


def test_format_sse_single_line_json() -> None:
    frame = format_sse("audit.sanitized", {"note": "dòng 1\ndòng 2", "n": 1})
    lines = frame.split("\n")
    assert lines[0] == "event: audit.sanitized"
    assert lines[1].startswith("data: ")
    assert json.loads(lines[1][6:]) == {"note": "dòng 1\ndòng 2", "n": 1}
    assert frame.endswith("\n\n")
    assert frame.count("\n") == 3


def test_file6_anti_buffering_headers() -> None:
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"
    assert SSE_HEADERS["Cache-Control"] == "no-cache, no-transform"


async def test_ec_db22_keepalive_ping_while_source_is_slow() -> None:
    async def slow() -> AsyncIterator[str]:
        await asyncio.sleep(0.25)
        yield format_sse("audit.sanitized", {"ok": True})

    frames = [f async for f in with_keepalive(slow(), interval_s=0.05)]
    assert frames[-1].startswith("event: audit.sanitized")
    pings = [f for f in frames if f.startswith("event: ping")]
    assert len(pings) >= 2
    Ping.model_validate_json(pings[0].split("data: ", 1)[1])


async def test_keepalive_no_ping_when_source_fast() -> None:
    async def fast() -> AsyncIterator[str]:
        for i in range(3):
            yield format_sse("x", {"i": i})

    frames = [f async for f in with_keepalive(fast(), interval_s=5)]
    assert len(frames) == 3


async def test_keepalive_cancels_source_on_close() -> None:
    cancelled = asyncio.Event()

    async def forever() -> AsyncIterator[str]:
        try:
            while True:
                await asyncio.sleep(10)
                yield "never"
        finally:
            cancelled.set()

    gen = with_keepalive(forever(), interval_s=0.01)
    first = await anext(gen)
    assert first.startswith("event: ping")
    await gen.aclose()
    await asyncio.wait_for(cancelled.wait(), timeout=1)
