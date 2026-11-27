"""Cổng dữ liệu của bàn làm việc Fraud Ops (Protocol) + bản in-memory cho test.

Dùng lại ``MuleGraphRepository`` của T14 (``graph``) cho đỉnh/cạnh/cụm; bổ sung phần T14 chưa có:
liệt kê cụm phân trang, tên bên cho vay, và ghi chuỗi băm kiểm toán khi unseal (EC-DB20).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.errors import AppError, ErrorCode
from app.core.schemas.ops import SeverityLevel
from app.db_schema.hashchain import GENESIS_HASH, compute_entry_hash
from app.mule_graph.models import ClusterRecord
from app.mule_graph.repository import InMemoryRepository, MuleGraphRepository


@dataclass(frozen=True, slots=True)
class ClusterCursor:
    last_seen_at: datetime
    cluster_id: UUID


def encode_cursor(c: ClusterCursor) -> str:
    raw = f"{c.last_seen_at.isoformat()}|{c.cluster_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value: str) -> ClusterCursor:
    """Con trỏ rác -> 422-ish (VALIDATION_ERROR) thay vì lỗi 500."""
    try:
        padded = value + "=" * (-len(value) % 4)
        ts, cid = base64.urlsafe_b64decode(padded.encode()).decode().split("|", 1)
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return ClusterCursor(parsed, UUID(cid))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise AppError(
            ErrorCode.VALIDATION_ERROR, 422, "Con trỏ phân trang không hợp lệ."
        ) from None


class OpsRepository(Protocol):
    @property
    def graph(self) -> MuleGraphRepository: ...

    async def list_clusters(
        self, *, severity: SeverityLevel | None, limit: int, after: ClusterCursor | None
    ) -> list[ClusterRecord]:
        """Sắp xếp ``last_seen_at DESC, id DESC``; trả tối đa ``limit`` bản ghi."""
        ...

    async def lender_names(self, ids: Sequence[UUID]) -> dict[UUID, str]: ...

    async def append_audit(
        self, actor_id: str, action_type: str, target_ref_id: UUID, at: datetime
    ) -> str:
        """Ghi 1 mục vào chuỗi băm bất biến và trả ``entry_hash``."""
        ...


class InMemoryOpsRepository:
    """Bản in-memory bọc ``InMemoryRepository`` của T14 (chỉ cho test/dev)."""

    def __init__(self, graph: InMemoryRepository | None = None) -> None:
        self._graph = graph or InMemoryRepository()
        self.lenders: dict[UUID, str] = {}
        self.audit_entries: list[tuple[str, str, UUID, datetime, str]] = []
        self.fail_audit = False

    @property
    def graph(self) -> InMemoryRepository:
        return self._graph

    async def list_clusters(
        self, *, severity: SeverityLevel | None, limit: int, after: ClusterCursor | None
    ) -> list[ClusterRecord]:
        rows = [c for c in self._graph.clusters.values() if severity in (None, c.severity_level)]
        rows.sort(key=lambda c: (c.last_seen_at, c.id), reverse=True)
        if after is not None:
            rows = [
                c for c in rows if (c.last_seen_at, c.id) < (after.last_seen_at, after.cluster_id)
            ]
        return rows[:limit]

    async def lender_names(self, ids: Sequence[UUID]) -> dict[UUID, str]:
        return {i: self.lenders[i] for i in ids if i in self.lenders}

    async def append_audit(
        self, actor_id: str, action_type: str, target_ref_id: UUID, at: datetime
    ) -> str:
        if self.fail_audit:
            raise RuntimeError("audit chain unavailable")
        prev = self.audit_entries[-1][4] if self.audit_entries else GENESIS_HASH
        digest = compute_entry_hash(prev, actor_id, action_type, target_ref_id, at)
        self.audit_entries.append((actor_id, action_type, target_ref_id, at, digest))
        return digest
