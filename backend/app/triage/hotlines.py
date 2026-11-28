"""Danh bạ tổng đài phong tỏa theo mã BIN ngân hàng (NAPAS).

Không có số điện thoại nào được viết cứng ở đây (không bịa dữ liệu): danh bạ được nạp từ nguồn đã
kiểm chứng (seed/DB) qua ``BankHotlineRegistry``. BIN chưa có trong danh bạ -> bản ghi dự phòng
``verified=False`` với hotline rỗng, và các bước hướng dẫn bảo người dùng gọi số in sau thẻ.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BankHotline:
    bank_bin: str
    short_name: str
    hotline: str
    verified: bool = False


class BankHotlineRegistry(Protocol):
    def lookup(self, bank_bin: str) -> BankHotline | None: ...


class InMemoryBankHotlineRegistry:
    """Danh bạ trong bộ nhớ (nạp từ seed đã kiểm chứng hoặc fixture test)."""

    def __init__(self, entries: Iterable[BankHotline] = ()) -> None:
        self._by_bin = {e.bank_bin: e for e in entries}

    def lookup(self, bank_bin: str) -> BankHotline | None:
        return self._by_bin.get(bank_bin)


def get_bank_hotline_registry() -> BankHotlineRegistry:
    """Dependency; mặc định RỖNG cho tới khi có nguồn đã kiểm chứng (ghi đè ở nơi nối dây)."""
    return InMemoryBankHotlineRegistry()
