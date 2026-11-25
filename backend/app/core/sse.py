"""Tiện ích Server-Sent Events dùng chung (File 4 §4.2(A), File 6 §1.2 mục 2, EC-DB22).

- Header chống gom gói qua proxy: ``X-Accel-Buffering: no``
  + ``Cache-Control: no-cache, no-transform``.
- Định dạng khung ``event: <tên>\\ndata: <json 1 dòng>\\n\\n``.
- ``with_keepalive`` chèn event ``ping`` mỗi ``interval_s`` giây khi nguồn chưa phát gì.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from typing import Any, Final

from pydantic import BaseModel

SSE_MEDIA_TYPE: Final = "text/event-stream"

SSE_HEADERS: Final[Mapping[str, str]] = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}

PING_EVENT: Final = "ping"


def format_sse(event: str, data: BaseModel | Mapping[str, Any], event_id: str | None = None) -> str:
    """Tạo 1 khung SSE. JSON luôn 1 dòng (không có ``\\n`` trong data)."""
    if isinstance(data, BaseModel):
        payload = data.model_dump_json()
    else:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {payload}")
    return "\n".join(lines) + "\n\n"


def ping_frame(started_monotonic: float) -> str:
    elapsed_ms = int((time.monotonic() - started_monotonic) * 1000)
    return format_sse(PING_EVENT, {"elapsed_ms": elapsed_ms})


async def with_keepalive(
    source: AsyncIterator[str], interval_s: float
) -> AsyncGenerator[str, None]:
    """Bọc một async iterator khung SSE: nếu sau ``interval_s`` chưa có khung mới thì phát ``ping``.

    Không giữ tài nguyên nào ngoài task đọc nguồn; hủy sạch khi client ngắt.
    """
    started = time.monotonic()
    iterator = aiter(source)
    pending: asyncio.Task[str] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=interval_s)
            if not done:
                yield ping_frame(started)
                continue
            try:
                frame = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            yield frame
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
