"""Cổng chế độ Gemini THẬT: mặc định TẮT. Chỉ bật khi người dùng đặt biến môi trường rõ ràng.

Cần đồng thời ``BENCH_LIVE=1`` và ``BENCH_GEMINI_API_KEY`` (khóa lấy từ môi trường process,
runner KHÔNG đọc/ghi file .env*). Khi bật: in cảnh báo chi phí + gửi dữ liệu ra ngoài.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_FLAG = "BENCH_LIVE"
ENV_KEY = "BENCH_GEMINI_API_KEY"
DEFAULT_MAX_LIVE_CALLS = 20

WARNING = (
    "CANH BAO: che do LIVE se gui du lieu benchmark (van ban dong tien tong hop) toi Gemini API "
    "cua Google, TON CHI PHI/quota that. Chi chay khi ban da duyet. "
    "Mac dinh runner chay OFFLINE bang client gia (chi kiem ha tang, KHONG do do chinh xac AI that)."
)


@dataclass(frozen=True, slots=True)
class LiveDecision:
    enabled: bool
    reason: str
    max_calls: int = 0


def decide_live(env: Mapping[str, str] | None = None) -> LiveDecision:
    """Quyết định bật live dựa trên biến môi trường (truyền ``env`` để test)."""
    e = os.environ if env is None else env
    if e.get(ENV_FLAG, "").strip() != "1":
        return LiveDecision(False, f"{ENV_FLAG}!=1 (mac dinh OFFLINE)")
    if not e.get(ENV_KEY, "").strip():
        return LiveDecision(False, f"{ENV_FLAG}=1 nhung thieu {ENV_KEY}; van chay OFFLINE")
    raw = e.get("BENCH_LIVE_MAX_CALLS", "").strip()
    try:
        cap = int(raw) if raw else DEFAULT_MAX_LIVE_CALLS
    except ValueError:
        cap = DEFAULT_MAX_LIVE_CALLS
    return LiveDecision(True, "BENCH_LIVE=1 + khoa API co trong moi truong", max(1, cap))
