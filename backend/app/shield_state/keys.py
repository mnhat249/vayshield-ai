"""Keyspace Redis ``vs:*`` + TTL (File 4 §3.1) và nạp script Lua.

Redis Cluster: ``atomic_smurf_and_risk_eval.lua`` chạm 2 khóa (``vs:smurf:win:{session_id}`` và
``vs:shield:state:{device_id}``) nên trên Cluster phải cùng slot. Giữ nguyên tên khóa theo đặc tả
(Redis đơn/Memorystore Standard); nếu chuyển sang Cluster thì bọc phần chung bằng hash tag, vd
``vs:shield:state:{<device_id>}`` và ``vs:smurf:win:{<device_id>}:<session_id>``.
"""

from __future__ import annotations

import hashlib
import re
from functools import cache
from pathlib import Path
from typing import Final

from app.shield_state.risk import HOUR_MS, MINUTE_MS

SHIELD_STATE_TTL_MIN_MS: Final = 24 * HOUR_MS  # vs:shield:state TTL 24h–72h
SHIELD_STATE_TTL_MAX_MS: Final = 72 * HOUR_MS
SMURF_WINDOW_TTL_MS: Final = 15 * MINUTE_MS
NOTIF_IDEM_TTL_MS: Final = 10 * MINUTE_MS
B2B_IDEM_TTL_MS: Final = 48 * HOUR_MS

_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{0,127}$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_LUA_DIR: Final = Path(__file__).with_name("lua")


def _segment(value: str, what: str) -> str:
    """Thành phần khóa phải an toàn (không ':' / ký tự lạ) để không thể chèn sang keyspace khác."""
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"{what} không hợp lệ cho khóa Redis")
    return value


def shield_state_key(device_id: str) -> str:
    return f"vs:shield:state:{_segment(device_id, 'device_id')}"


def smurf_window_key(session_id: str) -> str:
    return f"vs:smurf:win:{_segment(session_id, 'session_id')}"


def notif_idem_key(device_id: str, dedupe_hash: str) -> str:
    if not _HEX64_RE.fullmatch(dedupe_hash):
        raise ValueError("dedupe_hash phải là SHA-256 hex thường")
    return f"vs:idem:notif:{_segment(device_id, 'device_id')}:{dedupe_hash}"


def b2b_idem_key(partner_id: str, idempotency_key: str) -> str:
    """Khóa của đối tác không đạt mẫu an toàn → băm SHA-256 (vẫn tất định, không chèn được)."""
    key = (
        idempotency_key
        if _ID_RE.fullmatch(idempotency_key)
        else "h_" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    )
    return f"vs:idem:b2b:{_segment(partner_id, 'partner_id')}:{key}"


def shield_state_ttl_ms(now_ms: int, shield_expires_at_ms: int | None) -> int:
    remaining = 0 if shield_expires_at_ms is None else shield_expires_at_ms - now_ms
    return min(SHIELD_STATE_TTL_MAX_MS, max(SHIELD_STATE_TTL_MIN_MS, remaining))


@cache
def load_lua(name: str) -> str:
    return (_LUA_DIR / name).read_text(encoding="utf-8")
