"""SqlLegalRepository trên PostgreSQL thật (mark ``pg``): lọc hiệu lực, verdict, ANY(:c), 1-hop.

EC-AI16, EC-DB17.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ai_pipeline.legal_rag import LegalRagService, SqlLegalRepository

PREFIX = "T13PG_"
V = "V_T13_PG_TEST"
TODAY = date(2026, 11, 25)

_INS = text(
    "INSERT INTO legal_provision_nodes (clause_code, doc_type, doc_number, effective_date, "
    "is_currently_valid, title_vi, verbatim_content, plain_summary_vi, applicable_verdicts, "
    "embedding_768, verified) VALUES (:c, 'LAW', :n, :d, :valid, :t, :vb, :s, :v, "
    "array_fill(0.1::real, ARRAY[768])::vector, :ver)"
)

# code, valid, effective_date, verbatim, verified, verdicts
_ROWS = [
    ("OK", True, date(2020, 1, 1), "Nội dung kiểm thử hiệu lực.", True, [V]),
    ("UNVER", True, date(1970, 1, 1), "", False, [V]),
    ("EXPIRED", False, date(2010, 1, 1), "Văn bản cũ.", True, [V]),
    ("FUTURE", True, date(2099, 1, 1), "Văn bản tương lai.", True, [V]),
    ("OTHER", True, date(2020, 1, 1), "Không thuộc phán quyết.", True, ["V_OTHER"]),
    ("NEIGH", True, date(2020, 1, 1), "Node láng giềng.", True, ["V_OTHER"]),
]


@pytest.fixture
async def seeded(pg: AsyncEngine, pg_admin: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async with pg_admin.begin() as c:
        for code, valid, d, vb, ver, verdicts in _ROWS:
            await c.execute(
                _INS,
                {
                    "c": PREFIX + code,
                    "n": "so-" + code,
                    "d": d,
                    "valid": valid,
                    "t": "Tiêu đề " + code,
                    "vb": vb,
                    "s": "Tóm tắt " + code,
                    "v": verdicts,
                    "ver": ver,
                },
            )
        await c.execute(
            text(
                "INSERT INTO legal_provision_edges (source_clause, target_clause, relation_type) "
                "VALUES (:a, :b, 'GUIDES_IMPLEMENTATION'), (:a, :e, 'AMENDS')"
            ),
            {"a": PREFIX + "OK", "b": PREFIX + "NEIGH", "e": PREFIX + "EXPIRED"},
        )
    try:
        yield pg
    finally:
        async with pg_admin.begin() as c:
            await c.execute(
                text("DELETE FROM legal_provision_edges WHERE source_clause LIKE :p"),
                {"p": PREFIX + "%"},
            )
            await c.execute(
                text("DELETE FROM legal_provision_nodes WHERE clause_code LIKE :p"),
                {"p": PREFIX + "%"},
            )


@pytest.mark.pg
async def test_sql_filters_validity_and_verdict(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as s:
        nodes = await SqlLegalRepository(s).nodes_for_verdict(V, TODAY)
    assert {n.clause_code for n in nodes} == {PREFIX + "OK", PREFIX + "UNVER"}
    unver = next(n for n in nodes if n.clause_code == PREFIX + "UNVER")
    assert unver.verified is False and unver.date_unknown


@pytest.mark.pg
async def test_sql_valid_nodes_excludes_expired_and_future(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as s:
        codes = {
            n.clause_code
            for n in await SqlLegalRepository(s).valid_nodes(TODAY)
            if n.clause_code.startswith(PREFIX)
        }
    assert codes == {PREFIX + c for c in ("OK", "UNVER", "OTHER", "NEIGH")}


@pytest.mark.pg
async def test_sql_one_hop_neighbors_any_array_and_skip_expired(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as s:
        repo = SqlLegalRepository(s)
        n1 = await repo.neighbors([PREFIX + "OK"], TODAY)
        n2 = await repo.neighbors([PREFIX + "NEIGH"], TODAY)  # chiều ngược
        n0 = await repo.neighbors([], TODAY)
    assert {n.clause_code for n in n1} == {PREFIX + "NEIGH"}  # EXPIRED bị loại
    assert {n.clause_code for n in n2} == {PREFIX + "OK"}
    assert n0 == []


@pytest.mark.pg
async def test_sql_explain_verdict_end_to_end(seeded: AsyncEngine) -> None:
    async with AsyncSession(seeded) as s:
        exp = await LegalRagService(SqlLegalRepository(s)).explain_verdict(V)
    assert {c.clause_code for c in exp.citations} == {PREFIX + "OK"}
    assert {p.clause_code for p in exp.pending_sources} == {PREFIX + "UNVER"}
