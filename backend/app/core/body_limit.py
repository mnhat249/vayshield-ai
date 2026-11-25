"""Giới hạn kích thước body ở tầng ASGI (File 4 EC-DB05, File 6 EC-OPS13).

Chặn TRƯỚC khi FastAPI/multipart parser đọc vào RAM/đĩa tạm:

1. ``Content-Length`` khai vượt giới hạn → 413 ngay, không đọc body.
2. Body chunked / khai gian → đếm byte khi stream; vượt → dừng đọc, trả 413.

Giới hạn theo route (đường dẫn chính xác) cấu hình qua ``Settings``; còn lại dùng mặc định.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import ErrorCode, ErrorDetail, error_response

CONTRACT_STREAM_PATH: Final = "/api/v1/audits/contract-stream"
VOICE_TERMS_PATH: Final = "/api/v1/audits/voice-terms"

_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def route_limits(settings: Settings) -> dict[str, int]:
    return {
        CONTRACT_STREAM_PATH: settings.body_limit_contract_stream_bytes,
        VOICE_TERMS_PATH: settings.body_limit_voice_terms_bytes,
    }


class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(
        self, app: ASGIApp, default_limit: int, per_path: Mapping[str, int] | None = None
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.per_path = dict(per_path or {})

    def limit_for(self, path: str) -> int:
        return self.per_path.get(path.rstrip("/") or "/", self.default_limit)

    async def _reject(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        response = error_response(
            ErrorCode.PAYLOAD_TOO_LARGE,
            413,
            details=[ErrorDetail(field="body", issue=f"max_{limit}_bytes")],
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "GET") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return
        limit = self.limit_for(scope.get("path", ""))

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = -1
                if declared < 0 or declared > limit:
                    await self._reject(scope, receive, send, limit)
                    return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                return  # nuốt response của app; 413 được gửi ở dưới
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            # Parser có thể bọc _BodyTooLargeError thành lỗi khác; chỉ nuốt khi đã vượt giới hạn.
            if not exceeded:
                raise
        if exceeded and not response_started:
            await self._reject(scope, receive, send, limit)
