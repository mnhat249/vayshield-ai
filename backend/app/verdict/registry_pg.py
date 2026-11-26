"""``LenderRegistry`` đọc từ PostgreSQL (bảng ``licensed_lender_registry``, migration 0001).

Ma trận phán quyết là hàm thuần đồng bộ, nên repo PG nạp một **snapshot** bất biến (async) rồi
phục vụ Protocol đồng bộ từ bộ nhớ. Gọi ``refresh()`` khi khởi động / theo chu kỳ.

Chưa có cột ``aliases`` trong bảng → ``LenderRecord.aliases`` luôn rỗng. Muốn dùng alias cho
lookalike cần migration MỚI (task riêng, người duyệt) — không sửa migration đã apply.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.schemas.common import LegalCategory
from app.verdict.registry import InMemoryLenderRegistry, LenderRecord

__all__ = ["PgLenderRegistry", "load_lender_records", "record_from_row"]

_SELECT_ACTIVE: Final = text(
    """
    SELECT entity_code, legal_category, official_short_name, official_legal_name,
           official_domains, official_hotlines, android_packages, sandbox_cap_vnd
    FROM licensed_lender_registry
    WHERE is_active
      AND effective_from <= :today
      AND (effective_to IS NULL OR effective_to >= :today)
    ORDER BY entity_code
    """
)


def _packages(raw: Any) -> tuple[str, ...]:
    """``android_packages`` JSONB: mảng chuỗi hoặc mảng object ``{"pkg"|"package"|
    "package_name": ..., "sha256": ...}`` (khớp ``AndroidPackageSig`` của contract)."""
    if raw is None:
        return ()
    if isinstance(raw, str | bytes):
        raw = json.loads(raw)
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in ("pkg", "package", "package_name"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    out.append(value)
                    break
    return tuple(out)


def _texts(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    return tuple(str(x) for x in raw if x)


def record_from_row(row: Mapping[Any, Any]) -> LenderRecord:
    """Ánh xạ 1 dòng ``licensed_lender_registry`` → ``LenderRecord`` (thuần, test không cần PG)."""
    cap = row.get("sandbox_cap_vnd")
    return LenderRecord(
        entity_code=str(row["entity_code"]),
        legal_category=LegalCategory(str(row["legal_category"])),
        official_short_name=str(row["official_short_name"]),
        official_legal_name=str(row["official_legal_name"]),
        official_domains=_texts(row.get("official_domains")),
        official_hotlines=_texts(row.get("official_hotlines")),
        android_packages=_packages(row.get("android_packages")),
        sandbox_cap_vnd=None if cap is None else int(cap),
    )


async def load_lender_records(
    conn: AsyncConnection, today: date | None = None
) -> tuple[LenderRecord, ...]:
    """Đọc các pháp nhân đang hiệu lực tại ``today`` (mặc định hôm nay)."""
    result = await conn.execute(_SELECT_ACTIVE, {"today": today or date.today()})
    return tuple(record_from_row(row) for row in result.mappings())


class PgLenderRegistry:
    """Cài đặt ``LenderRegistry`` trên snapshot đọc từ PostgreSQL.

    Trước ``refresh()`` registry rỗng → mọi bên cho vay rơi vào nhánh "không phép" (an toàn:
    không bao giờ ra XANH nhờ registry chưa nạp).
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._snapshot = InMemoryLenderRegistry(())

    async def refresh(self, today: date | None = None) -> int:
        async with self._engine.connect() as conn:
            records = await load_lender_records(conn, today)
        self._snapshot = InMemoryLenderRegistry(records)
        return len(records)

    def active_records(self) -> Sequence[LenderRecord]:
        return self._snapshot.active_records()

    def get(self, entity_code: str) -> LenderRecord | None:
        return self._snapshot.get(entity_code)
