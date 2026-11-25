"""Log không chứa OTP/PII (CLAUDE.md §6, Luật 91/2025)."""

from __future__ import annotations

import io
import json
import logging

import pytest

from app.core.logging import (
    UVICORN_LOGGERS,
    JsonFormatter,
    PiiRedactingFilter,
    RedactingFormatter,
    configure_logging,
    redact,
    trace_id_var,
)


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("Ma OTP cua ban la 482913, khong chia se", "482913"),
        ("Mã xác thực: 1234 có hiệu lực 5 phút", "1234"),
        ("Your verification code is 99887766", "99887766"),
        ("goi 0909123456 ngay", "0909123456"),
        ("SĐT +84 909 123 456 đã đăng ký", "909 123 456"),
        ("so tk 0071000123456 vietcombank", "0071000123456"),
        ("CCCD 079203001234", "079203001234"),
        ("the 4111 1111 1111 1111", "4111 1111 1111 1111"),
        ("email nguyenvana@example.com", "nguyenvana@example.com"),
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
    ],
)
def test_redact_removes_otp_and_pii(raw: str, secret: str) -> None:
    out = redact(raw)
    assert secret not in out
    assert "REDACTED" in out


def test_redact_keeps_harmless_numbers() -> None:
    assert redact("Lãi 501%/năm trong 30 ngày, 2 ảnh") == "Lãi 501%/năm trong 30 ngày, 2 ảnh"


def test_filter_redacts_args_extra_and_adds_trace_id() -> None:
    stream = io.StringIO()
    logger = logging.getLogger("tests.core.pii")
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.addFilter(PiiRedactingFilter())
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    token = trace_id_var.set("trace-abc")
    try:
        logger.warning("otp nhan duoc: %s", "OTP 556677", extra={"phone": "0912345678"})
    finally:
        trace_id_var.reset(token)
        logger.removeHandler(handler)
    line = json.loads(stream.getvalue())
    assert "556677" not in stream.getvalue()
    assert "0912345678" not in stream.getvalue()
    assert line["trace_id"] == "trace-abc"
    assert line["phone"] == "[PHONE_REDACTED]"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("482913 là mã OTP của bạn", "482913"),
        ("1234 la ma xac thuc giao dich", "1234"),
        ("Ma cua ban la 482913", "482913"),
        ("Mã của quý khách là: 778899", "778899"),
        ("Your code is 5566", "5566"),
    ],
)
def test_redact_otp_with_trailing_or_sms_keyword(raw: str, secret: str) -> None:
    assert secret not in redact(raw)


@pytest.mark.parametrize(
    "token",
    [
        "019361a8-7c10-7000-8000-112233445566",
        "aeede0466b7348bcbacaaefca0be5f36",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ],
)
def test_redact_keeps_uuid_and_hex_identifiers(token: str) -> None:
    text = f"audit_id={token} trace"
    assert redact(text) == text


def test_redact_still_masks_account_next_to_uuid() -> None:
    out = redact("audit 019361a8-7c10-7000-8000-112233445566 stk 0071000123456")
    assert "019361a8-7c10-7000-8000-112233445566" in out
    assert "0071000123456" not in out


def _capture(formatter: logging.Formatter, name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    logger = logging.getLogger(f"tests.core.pii.{name}")
    logger.handlers.clear()
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.addFilter(PiiRedactingFilter())
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger, stream


@pytest.mark.parametrize(
    "formatter",
    [RedactingFormatter("%(levelname)s %(message)s"), JsonFormatter()],
    ids=["text", "json"],
)
def test_exception_traceback_is_redacted(formatter: logging.Formatter) -> None:
    logger, stream = _capture(formatter, type(formatter).__name__)
    try:
        raise ValueError("khach 0909123456 email nguyenvana@example.com loi")
    except ValueError:
        logger.exception("xu ly that bai")
    logger.warning("stack", stack_info=True, extra={"note": "goi 0912345678"})
    out = stream.getvalue()
    assert "ValueError" in out
    assert "0909123456" not in out
    assert "nguyenvana@example.com" not in out
    assert "0912345678" not in out


def test_configure_logging_filters_uvicorn_loggers() -> None:
    configure_logging("INFO", json_format=False)
    for name in UVICORN_LOGGERS:
        assert any(isinstance(f, PiiRedactingFilter) for f in logging.getLogger(name).filters)


def test_uvicorn_access_style_args_kept_as_tuple_and_redacted() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/x?phone=0909123456", "1.1", 200),
        None,
    )
    assert PiiRedactingFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert "0909123456" not in record.getMessage()
    assert record.args[4] == 200
