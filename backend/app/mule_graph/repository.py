"""Cổng truy cập dữ liệu của đồ thị Mule + webhook B2B (Protocol) và bản in-memory cho test.

Giả định D6b: máy dev không có PostgreSQL → logic nghiệp vụ nằm ở ``service.py`` /
``shield_service.py`` và chỉ phụ thuộc các Protocol này. Bản SQL (SQLAlchemy + RLS
``app.current_partner_id``) là việc nối dây sau khi T04 merge — xem báo cáo T14.

Bản in-memory mô phỏng các bảo đảm mà SQL phải có:
- ``transaction()``: tuần tự hóa (tương đương giao dịch ngắn) — EC-DB07.
- ``add_edge``: ``ON CONFLICT DO NOTHING`` trên ``(mule_node_id, reporter_device_id)``.
- ``refresh_quorum``: ``SET distinct_device_quorum = (SELECT COUNT(*) FROM edges ...)`` nguyên tử.
- Cô lập tenant: mọi truy vấn shield B2B có ``partner_id`` bắt buộc (mô phỏng RLS).
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.schemas.ops import SeverityLevel
from app.mule_graph.models import (
    ClusterRecord,
    DeviceMatch,
    MuleEdgeRecord,
    MuleNodeRecord,
    PartnerRecord,
    ShieldRecord,
)


class MuleGraphRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[None]: ...
    async def lock_scope(self, key: str) -> None:
        """Khóa tư vấn mức giao dịch theo ``key`` (dải VA hoặc STK) để tuần tự hóa các báo cáo
        cùng đỉnh/cùng dải — tránh mất cập nhật quorum và deadlock giữa các STK cùng dải."""
        ...

    async def find_node_by_hmacs(self, candidates: Sequence[str]) -> MuleNodeRecord | None: ...
    async def get_node(self, node_id: UUID) -> MuleNodeRecord | None: ...
    async def create_node(self, node: MuleNodeRecord) -> MuleNodeRecord:
        """``INSERT ... ON CONFLICT (account_hmac_sha256) DO NOTHING`` rồi trả bản ghi hiện có."""
        ...

    async def save_node(self, node: MuleNodeRecord) -> None: ...
    async def add_edge(self, edge: MuleEdgeRecord) -> bool:
        """True nếu chèn mới, False nếu thiết bị đã bỏ phiếu cho STK này (1 phiếu/thiết bị)."""
        ...

    async def refresh_quorum(self, node_id: UUID) -> int:
        """Đếm lại số thiết bị độc lập từ bảng cạnh và ghi vào đỉnh, trả giá trị mới."""
        ...

    async def edges_for_nodes(self, node_ids: Sequence[UUID]) -> list[MuleEdgeRecord]: ...
    async def find_nodes_by_va(
        self, bank_bin: str, va_prefix_hmac: str
    ) -> list[MuleNodeRecord]: ...
    async def get_cluster(self, cluster_id: UUID) -> ClusterRecord | None: ...
    async def save_cluster(self, cluster: ClusterRecord) -> None: ...
    async def nodes_in_cluster(self, cluster_id: UUID) -> list[MuleNodeRecord]: ...


class B2BRepository(Protocol):
    """Mọi hàm shield nhận ``partner_id`` (RLS tenant, EC-DB15)."""

    async def get_partner_by_key(self, partner_key: str) -> PartnerRecord | None: ...
    async def find_device_by_phone_hmacs(self, candidates: Sequence[str]) -> DeviceMatch | None: ...
    async def create_shield(self, shield: ShieldRecord) -> tuple[ShieldRecord, bool]:
        """``(bản ghi, created)``; trùng ``(partner_id, idempotency_key)`` → bản cũ, False."""
        ...

    async def get_shield(self, partner_id: UUID, idempotency_key: str) -> ShieldRecord | None: ...
    async def count_active_waves(self, lender_registry_id: UUID | None, since: datetime) -> int:
        """Số cụm ``CRITICAL_WAVE`` mạo danh cùng bên cho vay, ``last_seen_at >= since``."""
        ...


class InMemoryRepository:
    """Triển khai in-memory của cả hai Protocol (chỉ dùng cho test/dev)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.nodes: dict[UUID, MuleNodeRecord] = {}
        self.edges: list[MuleEdgeRecord] = []
        self.clusters: dict[UUID, ClusterRecord] = {}
        self.partners: dict[str, PartnerRecord] = {}
        self.devices_by_phone: dict[str, DeviceMatch] = {}
        self.shields: dict[tuple[UUID, str], ShieldRecord] = {}

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._lock:
            yield

    # --- MuleGraphRepository ---
    async def lock_scope(self, key: str) -> None:
        return None  # ``transaction()`` đã tuần tự hóa toàn cục

    async def find_node_by_hmacs(self, candidates: Sequence[str]) -> MuleNodeRecord | None:
        for node in self.nodes.values():
            if node.account_hmac in candidates:
                return dataclasses.replace(node)
        return None

    async def get_node(self, node_id: UUID) -> MuleNodeRecord | None:
        n = self.nodes.get(node_id)
        return dataclasses.replace(n) if n else None

    async def create_node(self, node: MuleNodeRecord) -> MuleNodeRecord:
        for existing in self.nodes.values():
            if existing.account_hmac == node.account_hmac:
                return dataclasses.replace(existing)
        self.nodes[node.id] = dataclasses.replace(node)
        return dataclasses.replace(node)

    async def save_node(self, node: MuleNodeRecord) -> None:
        self.nodes[node.id] = dataclasses.replace(node)

    async def add_edge(self, edge: MuleEdgeRecord) -> bool:
        for e in self.edges:
            if (
                e.mule_node_id == edge.mule_node_id
                and e.reporter_device_id == edge.reporter_device_id
            ):
                return False
        self.edges.append(edge)
        return True

    async def refresh_quorum(self, node_id: UUID) -> int:
        count = sum(1 for e in self.edges if e.mule_node_id == node_id)
        self.nodes[node_id].distinct_device_quorum = count
        return count

    async def edges_for_nodes(self, node_ids: Sequence[UUID]) -> list[MuleEdgeRecord]:
        wanted = set(node_ids)
        return [e for e in self.edges if e.mule_node_id in wanted]

    async def find_nodes_by_va(self, bank_bin: str, va_prefix_hmac: str) -> list[MuleNodeRecord]:
        return [
            dataclasses.replace(n)
            for n in self.nodes.values()
            if n.bank_bin == bank_bin and n.va_prefix_hmac == va_prefix_hmac
        ]

    async def get_cluster(self, cluster_id: UUID) -> ClusterRecord | None:
        c = self.clusters.get(cluster_id)
        return dataclasses.replace(c) if c else None

    async def save_cluster(self, cluster: ClusterRecord) -> None:
        self.clusters[cluster.id] = dataclasses.replace(cluster)

    async def nodes_in_cluster(self, cluster_id: UUID) -> list[MuleNodeRecord]:
        return [dataclasses.replace(n) for n in self.nodes.values() if n.cluster_id == cluster_id]

    # --- B2BRepository ---
    async def get_partner_by_key(self, partner_key: str) -> PartnerRecord | None:
        return self.partners.get(partner_key)

    async def find_device_by_phone_hmacs(self, candidates: Sequence[str]) -> DeviceMatch | None:
        for c in candidates:
            if c in self.devices_by_phone:
                return self.devices_by_phone[c]
        return None

    async def create_shield(self, shield: ShieldRecord) -> tuple[ShieldRecord, bool]:
        key = (shield.partner_id, shield.idempotency_key)
        if key in self.shields:
            return self.shields[key], False
        self.shields[key] = shield
        return shield, True

    async def get_shield(self, partner_id: UUID, idempotency_key: str) -> ShieldRecord | None:
        return self.shields.get((partner_id, idempotency_key))

    async def count_active_waves(self, lender_registry_id: UUID | None, since: datetime) -> int:
        if lender_registry_id is None:
            return 0
        return sum(
            1
            for c in self.clusters.values()
            if c.impersonated_lender_id == lender_registry_id
            and c.severity_level is SeverityLevel.CRITICAL_WAVE
            and c.last_seen_at >= since
        )
