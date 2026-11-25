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


def redact(text: str) -> str:
    """Che mọi PII/OTP đã biết trong chuỗi. Hàm thuần, không I/O."""
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out


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


class JsonFormatter(logging.Formatter):
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
            payload["exc"] = redact(self.formatException(record.exc_info))
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
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(trace_id)s] %(message)s")
        )
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Logger có sẵn filter che PII (kể cả khi handler bên ngoài không có filter)."""
    logger = logging.getLogger(name)
    if not any(isinstance(f, PiiRedactingFilter) for f in logger.filters):
        logger.addFilter(PiiRedactingFilter())
    return logger
