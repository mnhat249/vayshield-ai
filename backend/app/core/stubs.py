"""Đánh dấu handler còn là stub M0 (trả mock).

Test ``tests/contract/test_stub_responses.py`` chỉ gọi các handler mang dấu này; khi task sau
thay bằng logic thật và bỏ ``@stub``, test tự bỏ qua route đó (không chặn task song song).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

STUB_ATTR: Final = "__vayshield_stub__"


def stub[F: Callable[..., Any]](func: F) -> F:
    setattr(func, STUB_ATTR, True)
    return func


def is_stub(func: Callable[..., Any]) -> bool:
    return bool(getattr(func, STUB_ATTR, False))
