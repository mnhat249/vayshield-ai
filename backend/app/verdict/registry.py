"""Sổ đăng ký pháp nhân cho vay được cấp phép (File 1 §1.2; bảng ``licensed_lender_registry``).

``LenderRegistry`` là Protocol thuần đọc; bản in-memory dùng cho test/Sandbox. Repo PostgreSQL
(đọc ``licensed_lender_registry`` do T08 seed) cài đặt cùng Protocol ở task khác.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from app.core.schemas.common import LegalCategory

__all__ = [
    "P2P_SANDBOX_DEFAULT_CAP_VND",
    "InMemoryLenderRegistry",
    "LenderRecord",
    "LenderRegistry",
]

# Trần dư nợ 100 triệu đồng/khách/nền tảng (File 1 §1.2 mục 3, S2-EC17).
P2P_SANDBOX_DEFAULT_CAP_VND: Final = 100_000_000


@dataclass(frozen=True, slots=True, kw_only=True)
class LenderRecord:
    """Một dòng của ``licensed_lender_registry`` (cột cùng tên, ``aliases`` thêm cho lookalike)."""

    entity_code: str
    legal_category: LegalCategory
    official_short_name: str
    official_legal_name: str
    aliases: tuple[str, ...] = ()
    official_domains: tuple[str, ...] = ()
    official_hotlines: tuple[str, ...] = ()
    android_packages: tuple[str, ...] = ()
    sandbox_cap_vnd: int | None = None
    is_active: bool = True

    @property
    def names(self) -> tuple[str, ...]:
        return (self.official_short_name, self.official_legal_name, *self.aliases)

    @property
    def p2p_cap_vnd(self) -> int:
        return (
            self.sandbox_cap_vnd
            if self.sandbox_cap_vnd is not None
            else (P2P_SANDBOX_DEFAULT_CAP_VND)
        )


@runtime_checkable
class LenderRegistry(Protocol):
    def active_records(self) -> Sequence[LenderRecord]:
        """Mọi pháp nhân đang hiệu lực."""
        ...

    def get(self, entity_code: str) -> LenderRecord | None:
        """Tra theo ``entity_code``; ``None`` nếu không có hoặc hết hiệu lực."""
        ...


class InMemoryLenderRegistry:
    """Cài đặt ``LenderRegistry`` trong bộ nhớ (bất biến sau khởi tạo)."""

    def __init__(self, records: Iterable[LenderRecord]) -> None:
        items = tuple(records)
        codes = [r.entity_code for r in items]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate entity_code in registry")
        self._records = items
        self._by_code = {r.entity_code: r for r in items}

    def active_records(self) -> Sequence[LenderRecord]:
        return tuple(r for r in self._records if r.is_active)

    def get(self, entity_code: str) -> LenderRecord | None:
        record = self._by_code.get(entity_code)
        return record if record is not None and record.is_active else None
