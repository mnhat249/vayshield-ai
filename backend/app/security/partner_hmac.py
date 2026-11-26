"""Xác thực chữ ký webhook B2B (File 4 §4.2(B), EC-DB19).

``X-VayShield-Signature: sha256=HEX(HMAC_SHA256(partner_secret, timestamp + "." + raw_body))``,
cửa sổ ``|now - X-VayShield-Timestamp| <= B2B_SIGNATURE_WINDOW_S`` (mặc định 300s — chịu lệch NTP
~45s). Chống phát lại tuyệt đối do ``Idempotency-Key`` lưu 48h ở tầng route B2B.
"""

from __future__ import annotations

import hmac
import re
from typing import Final

from app.core.errors import AppError, ErrorCode

DEFAULT_WINDOW_S: Final = 300
_SIG_RE: Final = re.compile(r"sha256=([0-9a-fA-F]{64})")
_TS_RE: Final = re.compile(r"\d{1,12}")


def compute_partner_signature(secret: bytes, timestamp: str, raw_body: bytes) -> str:
    mac = hmac.new(secret, timestamp.encode("ascii") + b"." + raw_body, "sha256")
    return "sha256=" + mac.hexdigest()


def verify_partner_request(
    *,
    secret: bytes,
    signature_header: str | None,
    timestamp_header: str | None,
    raw_body: bytes,
    now_s: int,
    window_s: int = DEFAULT_WINDOW_S,
) -> int:
    """Trả timestamp đã kiểm; sai → ``AppError`` 401 (``SIGNATURE_INVALID`` / ``TIMESTAMP_...``).

    Kiểm chữ ký TRƯỚC cửa sổ thời gian để không lộ thông tin cho bên không có secret.
    """
    if not signature_header or not timestamp_header:
        raise AppError(ErrorCode.UNAUTHORIZED, 401)
    ts_text = timestamp_header.strip()
    sig_match = _SIG_RE.fullmatch(signature_header.strip())
    if sig_match is None or not _TS_RE.fullmatch(ts_text):
        raise AppError(ErrorCode.SIGNATURE_INVALID, 401)
    expected = compute_partner_signature(secret, ts_text, raw_body)
    provided = "sha256=" + sig_match.group(1).lower()
    if not hmac.compare_digest(expected.encode("ascii"), provided.encode("ascii")):
        raise AppError(ErrorCode.SIGNATURE_INVALID, 401)
    ts = int(ts_text)
    if abs(now_s - ts) > window_s:
        raise AppError(ErrorCode.TIMESTAMP_OUT_OF_WINDOW, 401)
    return ts
