"""Nạp khóa/bí mật từ Settings (``SecretStr``) — không bao giờ log hay repr giá trị.

Mọi lỗi cấu hình chỉ nêu TÊN biến môi trường, không nêu giá trị.
"""

from __future__ import annotations

import base64
import binascii
import hmac

from pydantic import SecretStr


class SecurityConfigError(RuntimeError):
    """Thiếu/sai khóa bí mật. Thông điệp chỉ chứa tên biến, không chứa giá trị."""


def decode_b64_secret(secret: SecretStr | None, env_name: str, expected_len: int) -> bytes:
    """Giải base64 (chuẩn hoặc urlsafe, có/không padding) một bí mật có độ dài cố định."""
    if secret is None:
        raise SecurityConfigError(f"Thiếu biến môi trường {env_name}")
    raw = secret.get_secret_value().strip()
    padded = raw + "=" * (-len(raw) % 4)
    try:
        if "-" in raw or "_" in raw:
            value = base64.urlsafe_b64decode(padded.encode("ascii"))
        else:
            value = base64.b64decode(padded.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise SecurityConfigError(f"{env_name} không phải base64 hợp lệ") from None
    if len(value) != expected_len:
        raise SecurityConfigError(f"{env_name} phải dài đúng {expected_len} byte sau base64")
    return value


def secret_bytes(secret: SecretStr | None, env_name: str, min_len: int = 32) -> bytes:
    """Pepper dạng chuỗi bất kỳ (UTF-8), tối thiểu ``min_len`` byte entropy danh nghĩa."""
    if secret is None:
        raise SecurityConfigError(f"Thiếu biến môi trường {env_name}")
    value = secret.get_secret_value().encode("utf-8")
    if len(value) < min_len:
        raise SecurityConfigError(f"{env_name} quá ngắn (tối thiểu {min_len} byte)")
    return value


def key_fingerprint(key: bytes, label: bytes, length: int = 8) -> bytes:
    """Định danh khóa một chiều (HMAC(key, label)) — không lộ khóa, dùng để chọn khóa giải mã."""
    return hmac.new(key, label, "sha256").digest()[:length]


def wipe(buffer: bytearray) -> None:
    """Ghi đè 0 lên vùng nhớ khóa tạm (memory hygiene, File 4 §5.1 mục 5) — best effort."""
    for i in range(len(buffer)):
        buffer[i] = 0
