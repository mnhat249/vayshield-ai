"""Che PII trước khi lưu / trả về (File 4 §5.2, CLAUDE.md §6: log không chứa OTP/PII).

Hàm thuần, tất định. Quy tắc (giả định an toàn — thà che thừa còn hơn lộ):

- SĐT Việt Nam / E.164 → ``[SĐT]``.
- Email → ``[EMAIL]``.
- Mã OTP: chuỗi 4–8 chữ số ngay sau từ khóa ``otp`` / ``mã xác thực`` / ``mã xác nhận`` →
  ``[OTP]``.
- Dãy ≥ 9 chữ số liền (STK/thẻ/CCCD) mà KHÔNG theo sau là đơn vị tiền (``đ``, ``vnd``, ``đồng``) →
  giữ 3 số cuối: ``***789``. Số tiền có dấu phân cách nghìn (``20.000.000``) không bị che.
- Tên chủ tài khoản: không che được bằng regex — bộ lưu trữ KHÔNG nhận trường đó (xem
  ``contexts``), chỉ giữ cờ "tài khoản cá nhân".
"""

from __future__ import annotations

import re
from typing import Final

from app.db_schema.sanitize import strip_control_chars

_PHONE_RE: Final = re.compile(r"(?<![\w.])(?:\+84|0084|84|0)[\s.\-]?\d(?:[\s.\-]?\d){8,9}(?![\w])")
_EMAIL_RE: Final = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_OTP_RE: Final = re.compile(
    r"(?i)(\b(?:otp|mã\s+xác\s+thực|mã\s+xác\s+nhận|mã\s+bảo\s+mật)\b[^\d\n]{0,20})(\d{4,8})\b"
)
_LONG_DIGITS_RE: Final = re.compile(
    r"(?<![\d.,])(\d{9,19})(?![\d])(?!\s*(?:đ|vnd|vnđ|đồng|dong)\b)", re.IGNORECASE
)


def mask_account(account_no: str) -> str:
    """``1903123498899`` → ``1903****8899`` (đủ dài); ngắn → ``***`` + 3 số cuối."""
    digits = account_no.strip()
    if len(digits) >= 10:
        return f"{digits[:4]}****{digits[-4:]}"
    return "***" + digits[-3:] if digits else "***"


def mask_pii(text: str) -> str:
    """Che SĐT, email, OTP, dãy số dài; tước ký tự điều khiển (EC-DB26)."""
    out = strip_control_chars(text)
    out = _EMAIL_RE.sub("[EMAIL]", out)
    out = _OTP_RE.sub(lambda m: f"{m.group(1)}[OTP]", out)
    out = _PHONE_RE.sub("[SĐT]", out)
    return _LONG_DIGITS_RE.sub(lambda m: "***" + m.group(1)[-3:], out)


def mask_optional(text: str | None, limit: int = 200) -> str | None:
    if text is None:
        return None
    return mask_pii(text)[:limit]
