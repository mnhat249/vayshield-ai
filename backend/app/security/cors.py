"""Chính sách CORS chặt (EC-DB24, EC-OPS18).

- Chỉ đúng danh sách origin sản phẩm (so khớp chính xác scheme://host[:port], không wildcard).
- Không cookie (``allow_credentials=False``): xác thực bằng header tùy chỉnh/Bearer nên miễn nhiễm
  CSRF; trang độc hại không đọc được phản hồi vì không có ``Access-Control-Allow-Origin``.
- Production: chỉ ``https://``.

``app/main.py`` (T00) đã gắn ``CORSMiddleware`` với ``allow_credentials=False``; module này cung cấp
kiểm tra cấu hình + hàm thuần ``is_origin_allowed`` để route/WS kiểm ``Origin`` khi bắt tay.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

from app.core.config import Settings, get_settings

_ALLOWED_SCHEMES: Final = frozenset({"http", "https"})


class CorsConfigError(ValueError):
    pass


def normalize_origin(origin: str) -> str:
    """``scheme://host[:port]`` chữ thường; ném lỗi nếu có path/query/userinfo/wildcard."""
    value = origin.strip()
    if "*" in value:
        raise CorsConfigError("origin không được chứa wildcard")
    parts = urlsplit(value)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES or not parts.hostname:
        raise CorsConfigError("origin phải là http(s)://host")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise CorsConfigError("origin không được có path/query/userinfo")
    host = parts.hostname.lower()
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme.lower()}://{host}{port}"


def validated_cors_origins(settings: Settings | None = None) -> list[str]:
    cfg = settings or get_settings()
    origins = [normalize_origin(o) for o in cfg.cors_origins]
    if cfg.is_production:
        insecure = [o for o in origins if not o.startswith("https://")]
        if insecure:
            raise CorsConfigError("production chỉ chấp nhận origin https://")
    return origins


def is_origin_allowed(origin: str | None, allowed: list[str]) -> bool:
    """So khớp chính xác. Không có ``Origin`` (app native/server-to-server) → True."""
    if origin is None:
        return True
    try:
        normalized = normalize_origin(origin)
    except CorsConfigError:
        return False
    return normalized in allowed
