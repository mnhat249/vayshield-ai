"""Bản PostgreSQL của ``OpsRepository`` (SQLAlchemy async).

- ``fraud_campaign_clusters``: phân trang keyset ``(last_seen_at, id) DESC``.
- ``licensed_lender_registry.official_short_name``: tên bên cho vay bị mạo danh.
- Chuỗi băm: ``SELECT append_audit_chain(actor, action, target)`` (SQL tự tính hash, khóa advisory,
  READ COMMITTED) rồi đọc ``entry_hash`` theo ``seq_no`` trả về.

Chưa chạy được trên máy dev (không có PostgreSQL): test tương ứng đánh dấu ``@pytest.mark.pg``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.copilot.repository import ClusterCursor
from app.core.schemas.ops import ScamTrap, SeverityLevel
from app.mule_graph.models import ClusterRecord
from app.mule_graph.repository_pg import SqlMuleGraphRepository

_SQL_LIST = text(
    "SELECT id, campaign_label, dominant_scam_trap, severity_level, first_seen_at, last_seen_at,"
    " incident_count_24h, impersonated_lender_id FROM fraud_campaign_clusters"
    " WHERE (CAST(:sev AS text) IS NULL OR severity_level = CAST(:sev AS text))"
    " AND (CAST(:ts AS timestamptz) IS NULL"
    "      OR (last_seen_at, id) < (CAST(:ts AS timestamptz), CAST(:cid AS uuid)))"
    " ORDER BY last_seen_at DESC, id DESC LIMIT :lim"
)
_SQL_LENDERS = text(
    "SELECT id, official_short_name FROM licensed_lender_registry WHERE id = ANY(:ids)"
)
_SQL_AUDIT = text("SELECT append_audit_chain(:actor, :action, :target)")
_SQL_AUDIT_HASH = text("SELECT entry_hash FROM tamper_evident_audit_chain WHERE seq_no = :s")


def _cluster(row: Any) -> ClusterRecord:
    return ClusterRecord(
        id=row.id,
        campaign_label=row.campaign_label,
        dominant_scam_trap=ScamTrap(row.dominant_scam_trap),
        severity_level=SeverityLevel(row.severity_level),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        incident_count_24h=row.incident_count_24h,
        impersonated_lender_id=row.impersonated_lender_id,
    )


class SqlOpsRepository:
    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], graph: SqlMuleGraphRepository
    ) -> None:
        self._sm = sessionmaker
        self._graph = graph

    @property
    def graph(self) -> SqlMuleGraphRepository:
        return self._graph

    async def list_clusters(
        self, *, severity: SeverityLevel | None, limit: int, after: ClusterCursor | None
    ) -> list[ClusterRecord]:
        params: dict[str, Any] = {
            "sev": severity.value if severity else None,
            "ts": after.last_seen_at if after else None,
            "cid": str(after.cluster_id) if after else None,
            "lim": limit,
        }
        async with self._sm() as s, s.begin():
            rows = (await s.execute(_SQL_LIST, params)).all()
        return [_cluster(r) for r in rows]

    async def lender_names(self, ids: Sequence[UUID]) -> dict[UUID, str]:
        if not ids:
            return {}
        async with self._sm() as s, s.begin():
            rows = (await s.execute(_SQL_LENDERS, {"ids": list(ids)})).all()
        return {r.id: r.official_short_name for r in rows}

    async def append_audit(
        self, actor_id: str, action_type: str, target_ref_id: UUID, at: datetime
    ) -> str:
        async with self._sm() as s, s.begin():
            seq = (
                await s.execute(
                    _SQL_AUDIT,
                    {"actor": actor_id, "action": action_type, "target": target_ref_id},
                )
            ).scalar_one()
            digest = (await s.execute(_SQL_AUDIT_HASH, {"s": seq})).scalar_one()
        return str(digest)
