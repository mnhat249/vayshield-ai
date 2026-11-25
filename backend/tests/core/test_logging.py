"""Log không chứa OTP/PII (CLAUDE.md §6, Luật 91/2025)."""

from __future__ import annotations

import io
import json
import logging

import pytest

from app.core.logging import JsonFormatter, PiiRedactingFilter, redact, trace_id_var


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
