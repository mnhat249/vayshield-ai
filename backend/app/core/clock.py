"""Đồng hồ trừu tượng để test thời gian tất định (leo thang 10s, TTL, bán rã...).

Code nghiệp vụ nhận ``Clock`` qua tham số hoặc ``Depends(get_clock)``; test override bằng
``FakeClock``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Thời điểm hiện tại, timezone-aware UTC."""
        ...

    def monotonic(self) -> float:
        """Giây đơn điệu (không bị NTP chỉnh)."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """Đồng hồ giả: chỉ chạy khi gọi ``advance``."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 11, 25, 1, 0, 0, tzinfo=UTC)
        if self._now.tzinfo is None:
            raise ValueError("start phải có tzinfo")
        self._mono = 1_000.0

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("không thể lùi đồng hồ")
        self._now += timedelta(seconds=seconds)
        self._mono += seconds


_system_clock = SystemClock()


def get_clock() -> Clock:
    """FastAPI dependency; test dùng ``app.dependency_overrides[get_clock]``."""
    return _system_clock
