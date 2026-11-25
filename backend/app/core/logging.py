"""Logging có lọc PII/OTP (CLAUDE.md §6, File 4 §5.2 Luật 91/2025).

Mọi log đi qua ``PiiRedactingFilter``: che OTP, số điện thoại, số tài khoản/thẻ, CCCD, email,
token Bearer. Lọc áp dụng cho cả ``msg``, ``args`` và các thuộc tính ``extra`` dạng chuỗi.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

REDACTED: Final = "[REDACTED]"

# Thứ tự quan trọng: mẫu cụ thể (OTP có ngữ cảnh, email, bearer) chạy trước mẫu số chung.
_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # OTP có từ khóa ngữ cảnh: "OTP 123456", "mã xác thực: 1234", "ma OTP la 987654"
    (
        re.compile(
            r"(?i)(otp|mã\s*xác\s*(?:thực|nhận)|ma\s*xac\s*(?:thuc|nhan)|verification\s*code|"
            r"passcode|mã\s*giao\s*dịch|ma\s*giao\s*dich)(\D{0,12})\d{4,8}"
        ),
        r"\1\2[OTP_REDACTED]",
    ),
    # OTP có từ khóa đứng SAU: "482913 là mã OTP", "1234 la ma xac thuc"
    (
        re.compile(
            r"(?i)(?<!\d)\d{4,8}(?=\s*(?:l[àa]\s+)?(?:m[ãa]\s+)?(?:otp|x[áa]c\s*(?:th[ựu]c|"
            r"nh[ậa]n)|giao\s*d[ịi]ch|passcode))"
        ),
        "[OTP_REDACTED]",
    ),
    # Mẫu tin nhắn "Mã của bạn là 482913" / "Ma cua ban la 482913" / "your code is 1234"
    (
        re.compile(
            r"(?i)((?:m[ãa]\s+(?:c[ủu]a\s+)?(?:b[ạa]n|qu[ýy]\s+kh[áa]ch)\s+l[àa]|"
            r"your\s+code\s+is)\s*:?\s*)\d{4,8}"
        ),
        r"\1[OTP_REDACTED]",
    ),
    # Bearer / JWT
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer [TOKEN_REDACTED]"),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
        "[JWT_REDACTED]",
    ),
    # Email
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL_REDACTED]"),
    # SĐT Việt Nam: +84 / 84 / 0 + đầu số di động, cho phép dấu cách/chấm/gạch
    (
        re.compile(r"(?<![\d])(?:\+?84|0)[\s.-]?[35789](?:[\s.-]?\d){8}(?![\d])"),
        "[PHONE_REDACTED]",
    ),
    # Chuỗi số dài 9-19 chữ số (STK, số thẻ, CCCD 12 số) — cho phép dấu cách/gạch giữa nhóm
    (re.compile(r"(?<![\d])\d(?:[\s-]?\d){8,18}(?![\d])"), "[NUMBER_REDACTED]"),
)


# UUID / chuỗi hex dài (trace_id, hash, khóa idempotency) là định danh kỹ thuật, không phải PII:
# tạm thay bằng placeholder để mẫu "số dài" không ăn mất phần số của chúng.
_SAFE_TOKEN_RE: Final = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
    r"|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{16,}\b"
)
_PLACEHOLDER_RE: Final = re.compile("\x00(\\d+)\x00")


def redact(text: str) -> str:
    """Che mọi PII/OTP đã biết trong chuỗi. Hàm thuần, không I/O."""
    saved: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        saved.append(match.group(0))
        return f"\x00{len(saved) - 1}\x00"

    out = _SAFE_TOKEN_RE.sub(_protect, text.replace("\x00", ""))
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return _PLACEHOLDER_RE.sub(lambda m: saved[int(m.group(1))], out)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_redact_value(v) for v in value)
    return value


_STD_ATTRS: Final = frozenset(
    vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None)).keys()
    | {"message", "asctime", "trace_id"}
)


class PiiRedactingFilter(logging.Filter):
    """Filter che PII trên mọi LogRecord, đồng thời gắn ``trace_id``."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._redact_args_in_place(record):
            try:
                message = record.getMessage()
            except Exception:  # args không khớp format — vẫn che chuỗi gốc
                message = str(record.msg)
            record.msg = redact(message)
            record.args = None
        for key, value in list(vars(record).items()):
            if key not in _STD_ATTRS:
                setattr(record, key, _redact_value(value))
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        record.trace_id = trace_id_var.get()
        return True

    @staticmethod
    def _redact_args_in_place(record: logging.LogRecord) -> bool:
        """Giữ cấu trúc ``msg % args`` (uvicorn AccessFormatter cần ``args`` dạng tuple) nếu
        che từng arg chuỗi là đủ sạch; trả False để gộp thành chuỗi đã che."""
        if not record.args or not isinstance(record.msg, str):
            return False
        if redact(record.msg) != record.msg:
            return False
        redacted_args = _redact_value(record.args)
        try:
            candidate = record.msg % redacted_args
        except Exception:
            return False
        if redact(candidate) != candidate:
            return False
        record.args = redacted_args
        return True


class RedactingFormatter(logging.Formatter):
    """Formatter text: traceback/stack cũng đi qua ``redact`` (giá trị cục bộ, thông điệp
    exception có thể chứa SĐT/email)."""

    def formatException(self, ei: Any) -> str:  # noqa: N802 - API của logging
        return redact(super().formatException(ei))

    def formatStack(self, stack_info: str) -> str:  # noqa: N802 - API của logging
        return redact(super().formatStack(stack_info))

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(RedactingFormatter):
    """Định dạng JSON 1 dòng (thân thiện Cloud Logging)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", trace_id_var.get()),
        }
        for key, value in vars(record).items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    """Cấu hình root logger: 1 handler stdout + filter che PII. Gọi lặp lại an toàn."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        if getattr(handler, "_vayshield", False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler._vayshield = True  # type: ignore[attr-defined]
    handler.addFilter(PiiRedactingFilter())
    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(name)s [%(trace_id)s] %(message)s")
        )
    root.addHandler(handler)
    # uvicorn có handler riêng (access log chứa query string, có thể có ticket/SĐT).
    for name in UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        if not any(isinstance(f, PiiRedactingFilter) for f in uv_logger.filters):
            uv_logger.addFilter(PiiRedactingFilter())
        for uv_handler in uv_logger.handlers:
            if not any(isinstance(f, PiiRedactingFilter) for f in uv_handler.filters):
                uv_handler.addFilter(PiiRedactingFilter())


UVICORN_LOGGERS: Final = ("uvicorn", "uvicorn.error", "uvicorn.access")


def get_logger(name: str) -> logging.Logger:
    """Logger có sẵn filter che PII (kể cả khi handler bên ngoài không có filter)."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, PiiRedactingFilter) for f in logger.filters):
        logger.addFilter(PiiRedactingFilter())
    return logger
