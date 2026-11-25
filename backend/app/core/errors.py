"""Mã lỗi chuẩn + global exception handler (EC-OPS23).

Không bao giờ trả HTML hay stacktrace: mọi lỗi là JSON
``{"error": {"code", "message_vi", "trace_id", "details"}}`` với ``Content-Type: application/json``.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger, trace_id_var

logger = get_logger("vayshield.errors")

TRACE_HEADER: Final = "X-Trace-Id"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    TIMESTAMP_OUT_OF_WINDOW = "TIMESTAMP_OUT_OF_WINDOW"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    TICKET_INVALID_OR_CONSUMED = "TICKET_INVALID_OR_CONSUMED"
    PAIRING_CODE_INVALID = "PAIRING_CODE_INVALID"
    PAIRING_LOCKED = "PAIRING_LOCKED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    RATE_LIMITED = "RATE_LIMITED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    SERVICE_DEGRADED = "SERVICE_DEGRADED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


DEFAULT_MESSAGES_VI: Final[dict[ErrorCode, str]] = {
    ErrorCode.VALIDATION_ERROR: "Dữ liệu gửi lên chưa hợp lệ. Vui lòng kiểm tra lại các trường.",
    ErrorCode.MALFORMED_REQUEST: "Yêu cầu bị hỏng hoặc sai định dạng.",
    ErrorCode.UNAUTHORIZED: "Thiếu hoặc sai thông tin xác thực.",
    ErrorCode.SIGNATURE_INVALID: "Chữ ký không hợp lệ.",
    ErrorCode.TIMESTAMP_OUT_OF_WINDOW: "Dấu thời gian nằm ngoài cửa sổ cho phép.",
    ErrorCode.FORBIDDEN: "Bạn không có quyền thực hiện thao tác này.",
    ErrorCode.NOT_FOUND: "Không tìm thấy tài nguyên yêu cầu.",
    ErrorCode.METHOD_NOT_ALLOWED: "Phương thức không được hỗ trợ cho đường dẫn này.",
    ErrorCode.CONFLICT: "Yêu cầu xung đột với trạng thái hiện tại.",
    ErrorCode.IDEMPOTENCY_CONFLICT: "Idempotency-Key đã dùng với nội dung khác.",
    ErrorCode.TICKET_INVALID_OR_CONSUMED: "Vé kết nối không hợp lệ hoặc đã được dùng.",
    ErrorCode.PAIRING_CODE_INVALID: "Mã ghép đôi không đúng hoặc đã hết hạn.",
    ErrorCode.PAIRING_LOCKED: "Mã ghép đôi đã bị khóa do nhập sai quá số lần cho phép.",
    ErrorCode.PAYLOAD_TOO_LARGE: "Dữ liệu tải lên vượt quá dung lượng cho phép.",
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: "Định dạng tệp không được hỗ trợ.",
    ErrorCode.RATE_LIMITED: "Bạn thao tác quá nhanh. Vui lòng thử lại sau ít phút.",
    ErrorCode.BUDGET_EXCEEDED: "Hệ thống tạm đạt giới hạn sử dụng. Vui lòng thử lại sau.",
    ErrorCode.UPSTREAM_TIMEOUT: "Dịch vụ phân tích phản hồi chậm. Vui lòng thử lại.",
    ErrorCode.SERVICE_DEGRADED: "Hệ thống đang chạy ở chế độ hạ cấp. Vui lòng dùng nhập tay.",
    ErrorCode.INTERNAL_ERROR: "Hệ thống gặp sự cố ngoài dự kiến. Vui lòng thử lại sau.",
}

_STATUS_TO_CODE: Final[dict[int, ErrorCode]] = {
    400: ErrorCode.MALFORMED_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    423: ErrorCode.PAIRING_LOCKED,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.SERVICE_DEGRADED,
    504: ErrorCode.UPSTREAM_TIMEOUT,
}


class ErrorDetail(BaseModel):
    field: str = Field(description="Đường dẫn trường lỗi, vd 'body.images'")
    issue: str = Field(description="Mô tả ngắn (không chứa dữ liệu người dùng)")


class ErrorBody(BaseModel):
    code: ErrorCode
    message_vi: str
    trace_id: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AppError(Exception):
    """Lỗi nghiệp vụ có mã chuẩn. Raise ở bất kỳ đâu trong route/service."""

    def __init__(
        self,
        code: ErrorCode,
        status_code: int,
        message_vi: str | None = None,
        details: list[ErrorDetail] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.message_vi = message_vi or DEFAULT_MESSAGES_VI[code]
        self.details = details
        self.headers = headers
        super().__init__(f"{code}: {self.message_vi}")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def current_trace_id() -> str:
    value = trace_id_var.get()
    return value if value != "-" else new_trace_id()


def error_payload(
    code: ErrorCode, message_vi: str | None = None, details: list[ErrorDetail] | None = None
) -> dict[str, Any]:
    body = ErrorBody(
        code=code,
        message_vi=message_vi or DEFAULT_MESSAGES_VI[code],
        trace_id=current_trace_id(),
        details=details,
    )
    return ErrorResponse(error=body).model_dump(mode="json", exclude_none=True)


def error_response(
    code: ErrorCode,
    status_code: int,
    message_vi: str | None = None,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message_vi, details),
        headers=headers,
    )


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    return error_response(exc.code, exc.status_code, exc.message_vi, exc.details, exc.headers)


async def _http_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    if exc.status_code < 500 and code is ErrorCode.INTERNAL_ERROR:
        code = ErrorCode.MALFORMED_REQUEST
    return error_response(code, exc.status_code, headers=getattr(exc, "headers", None))


async def _validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    details: list[ErrorDetail] = []
    for err in exc.errors()[:20]:
        loc = ".".join(str(part) for part in err.get("loc", ()))
        # Không trả lại giá trị đầu vào ("input") để tránh phản chiếu PII.
        details.append(ErrorDetail(field=loc or "body", issue=str(err.get("type", "invalid"))))
    return error_response(ErrorCode.VALIDATION_ERROR, 422, details=details)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)


class TraceAndCatchAllMiddleware:
    """ASGI middleware: gắn trace_id cho mỗi request + bắt MỌI exception chưa xử lý → JSON 500.

    Không re-raise (khác ServerErrorMiddleware của Starlette) nên không bao giờ lộ HTML/traceback.
    Nếu response đã bắt đầu (vd SSE đang stream) thì chỉ log và đóng kết nối.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        trace_id = new_trace_id()
        token = trace_id_var.set(trace_id)
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = list(message.get("headers", []))
                headers.append((TRACE_HEADER.lower().encode(), trace_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception("unhandled_exception", extra={"path": scope.get("path", "")})
            if scope["type"] == "http" and not started:
                response = error_response(ErrorCode.INTERNAL_ERROR, 500)
                await response(scope, receive, send_wrapper)
        finally:
            trace_id_var.reset(token)
