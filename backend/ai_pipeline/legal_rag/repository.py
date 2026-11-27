"""Kho node pháp lý: in-memory (test/sandbox) và SQL (PostgreSQL, bộ lọc hiệu lực cứng)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LegalEdge, LegalNode


class LegalRepository(Protocol):
    """Mọi phương thức CHỈ trả node còn hiệu lực tại ``today`` (EC-AI16, EC-DB17)."""

    async def nodes_for_verdict(self, verdict_code: str, today: date) -> list[LegalNode]: ...

    async def valid_nodes(self, today: date) -> list[LegalNode]: ...

    async def neighbors(self, clause_codes: Iterable[str], today: date) -> list[LegalNode]: ...


class InMemoryLegalRepository:
    def __init__(self, nodes: Iterable[LegalNode], edges: Iterable[LegalEdge] = ()) -> None:
        self._nodes = {n.clause_code: n for n in nodes}
        self._edges = list(edges)

    async def nodes_for_verdict(self, verdict_code: str, today: date) -> list[LegalNode]:
        return [
            n
            for n in self._nodes.values()
            if verdict_code in n.applicable_verdicts and n.is_in_force(today)
        ]

    async def valid_nodes(self, today: date) -> list[LegalNode]:
        return [n for n in self._nodes.values() if n.is_in_force(today)]

    async def neighbors(self, clause_codes: Iterable[str], today: date) -> list[LegalNode]:
        seeds = set(clause_codes)
        out: dict[str, LegalNode] = {}
        for e in self._edges:
            for a, b in ((e.source_clause, e.target_clause), (e.target_clause, e.source_clause)):
                node = self._nodes.get(b)
                if a in seeds and node is not None and node.is_in_force(today):
                    out[b] = node
        return list(out.values())


_COLS = (
    "clause_code, doc_type, doc_number, effective_date, is_currently_valid, title_vi, "
    "verbatim_content, plain_summary_vi, applicable_verdicts, verified"
)
_VALID = "is_currently_valid = TRUE AND effective_date <= :today"


def _row_to_node(m: Any) -> LegalNode:
    return LegalNode(
        clause_code=m["clause_code"],
        doc_type=m["doc_type"],
        doc_number=m["doc_number"],
        effective_date=m["effective_date"],
        is_currently_valid=m["is_currently_valid"],
        title_vi=m["title_vi"],
        verbatim_content=m["verbatim_content"],
        plain_summary_vi=m["plain_summary_vi"],
        applicable_verdicts=tuple(m["applicable_verdicts"]),
        verified=bool(m["verified"]),
    )


class SqlLegalRepository:
    """Truy vấn PostgreSQL; kho < 2.000 điều khoản (EC-DB17) nên nạp theo bộ lọc rồi chấm điểm.

    Embedding trong DB là vector giả của seed nên KHÔNG đọc; điểm cosine tính qua ``Embedder``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def nodes_for_verdict(self, verdict_code: str, today: date) -> list[LegalNode]:
        res = await self._s.execute(
            text(
                f"SELECT {_COLS} FROM legal_provision_nodes "  # noqa: S608 - hằng số nội bộ
                f"WHERE {_VALID} AND :v = ANY(applicable_verdicts) ORDER BY clause_code"
            ),
            {"today": today, "v": verdict_code},
        )
        return [_row_to_node(r._mapping) for r in res]

    async def valid_nodes(self, today: date) -> list[LegalNode]:
        res = await self._s.execute(
            text(
                f"SELECT {_COLS} FROM legal_provision_nodes "  # noqa: S608
                f"WHERE {_VALID} ORDER BY clause_code"
            ),
            {"today": today},
        )
        return [_row_to_node(r._mapping) for r in res]

    async def neighbors(self, clause_codes: Iterable[str], today: date) -> list[LegalNode]:
        codes = list(clause_codes)
        if not codes:
            return []
        res = await self._s.execute(
            text(
                f"SELECT {_COLS} FROM legal_provision_nodes "  # noqa: S608
                f"WHERE {_VALID} AND clause_code IN ("
                "SELECT target_clause FROM legal_provision_edges WHERE source_clause = ANY(:c) "
                "UNION SELECT source_clause FROM legal_provision_edges "
                "WHERE target_clause = ANY(:c)) ORDER BY clause_code"
            ),
            {"today": today, "c": codes},
        )
        return [_row_to_node(r._mapping) for r in res]
