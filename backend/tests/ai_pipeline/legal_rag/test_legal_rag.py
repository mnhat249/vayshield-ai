"""T13: Legal GraphRAG gắn phán quyết.

Edge case: EC-AI14 (drift ngôn ngữ), EC-AI16 (văn bản hết hiệu lực), EC-AI17 (domain guardrail),
EC-DB17 (lọc hiệu lực kèm truy hồi), zero legal hallucination.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from ai_pipeline.legal_rag import (
    InMemoryLegalRepository,
    LegalEdge,
    LegalNode,
    LegalRagService,
    LexicalHashEmbedder,
    is_in_scope,
)
from app.core.clock import FakeClock
from app.core.verdict_codes import VerdictCode
from seeds.data import EDGES, PROVISIONS

TODAY = datetime(2026, 11, 25, tzinfo=UTC)


def seed_nodes() -> list[LegalNode]:
    return [
        LegalNode(
            clause_code=p.clause_code,
            doc_type=p.doc_type,
            doc_number=p.doc_number,
            effective_date=date.fromisoformat(p.effective_date),
            is_currently_valid=True,
            title_vi=p.title_vi,
            verbatim_content=p.verbatim_content,
            plain_summary_vi=p.plain_summary_vi,
            applicable_verdicts=p.applicable_verdicts,
            verified=p.verified,
        )
        for p in PROVISIONS
    ]


def seed_edges() -> list[LegalEdge]:
    return [LegalEdge(s, t, r) for s, t, r in EDGES]


def make(nodes: list[LegalNode] | None = None, **kw: object) -> LegalRagService:
    repo = InMemoryLegalRepository(nodes if nodes is not None else seed_nodes(), seed_edges())
    return LegalRagService(repo, clock=FakeClock(TODAY), **kw)  # type: ignore[arg-type]


@pytest.mark.parametrize("verdict", list(VerdictCode))
async def test_zero_legal_hallucination_citations_within_applicable(verdict: VerdictCode) -> None:
    """Zero hallucination: mọi citation thuộc node có verdict trong applicable_verdicts."""
    by_code = {n.clause_code: n for n in seed_nodes()}
    exp = await make().explain_verdict(verdict.value)
    for c in exp.citations:
        assert verdict.value in by_code[c.clause_code].applicable_verdicts
    for p in exp.pending_sources:
        assert verdict.value in by_code[p.clause_code].applicable_verdicts


async def test_explain_does_not_pull_graph_neighbours() -> None:
    """Chiều 1 không mở rộng 1-hop: V_RED_CRIMINAL_USURY không được trích TCTD_2024_D100."""
    exp = await make().explain_verdict("V_RED_CRIMINAL_USURY")
    codes = {c.clause_code for c in exp.citations} | {p.clause_code for p in exp.pending_sources}
    assert "TCTD_2024_D100" not in codes
    assert "BLDS_2015_D468" in codes


async def test_only_verbatim_nodes_are_cited_and_unverified_flagged() -> None:
    """Không bịa nguyên văn: node rỗng verbatim chỉ vào pending_sources; unverified có ghi chú."""
    exp = await make().explain_verdict("V_RED_CRIMINAL_USURY")
    assert [c.clause_code for c in exp.citations] == ["BLDS_2015_D468"]
    cit = exp.citations[0]
    assert cit.verified is False
    assert cit.doc_number is None and cit.effective_date is None
    assert cit.note and "chưa" in cit.note
    assert {p.clause_code for p in exp.pending_sources} == {"BLHS_2015_D201", "NQ_01_2021_HDTP"}
    assert exp.fully_verified is False
    assert "chưa được đối chiếu" in exp.plain_vi


async def test_verified_node_shows_doc_number_and_date() -> None:
    node = replace(
        seed_nodes()[0],
        verified=True,
        doc_number="91/2015/QH13",
        effective_date=date(2017, 1, 1),
        verbatim_content="Nội dung nguyên văn thử nghiệm.",
    )
    exp = await make([node]).explain_verdict(node.applicable_verdicts[0])
    assert exp.fully_verified is True
    c = exp.citations[0]
    assert c.doc_number == "91/2015/QH13" and c.effective_date == date(2017, 1, 1)
    d = exp.to_dict()
    assert d["citations"][0]["doc_number"] == "91/2015/QH13"  # type: ignore[index]
    assert "chưa được đối chiếu" not in exp.plain_vi


async def test_ec_ai16_expired_and_future_documents_not_returned() -> None:
    """EC-AI16/EC-DB17: is_currently_valid=False hoặc effective_date tương lai bị loại."""
    base = seed_nodes()[0]
    v = base.applicable_verdicts[0]
    old = replace(base, clause_code="OLD", is_currently_valid=False, title_vi="Luật TCTD 2010")
    future = replace(base, clause_code="FUT", effective_date=date(2030, 1, 1))
    cur = replace(base, clause_code="CUR")
    svc = make([old, future, cur])
    exp = await svc.explain_verdict(v)
    assert {c.clause_code for c in exp.citations} | {
        p.clause_code for p in exp.pending_sources
    } == {"CUR"}
    out = await svc.hybrid_search("lãi suất vay")
    assert {h.clause_code for h in out.hits} == {"CUR"}


async def test_ec_ai16_expansion_skips_expired_neighbour() -> None:
    nodes = seed_nodes()
    nodes = [
        replace(n, is_currently_valid=False) if n.clause_code == "BLDS_2015_D468" else n
        for n in nodes
    ]
    out = await make(nodes).hybrid_search("tội cho vay lãi nặng")
    assert "BLDS_2015_D468" not in {h.clause_code for h in out.hits}


async def test_hybrid_weights_and_ranking() -> None:
    out = await make().hybrid_search("trần lãi suất 20%/năm Bộ luật Dân sự", top_k=3)
    assert out.hits[0].clause_code == "BLDS_2015_D468"
    for h in out.hits:
        if not h.via_expansion:
            assert h.score == pytest.approx(0.65 * h.cosine + 0.35 * h.bm25)
    assert out.hits[0].score >= out.hits[1].score


async def test_one_hop_expansion_adds_exempting_clause() -> None:
    """Mở rộng 1-hop: top-1 là BLDS D468 kéo theo TCTD_2024_D100 (cạnh ngoại lệ)."""
    out = await make().hybrid_search("trần lãi suất 20%/năm Bộ luật Dân sự", top_k=1)
    assert [h.clause_code for h in out.hits if not h.via_expansion] == ["BLDS_2015_D468"]
    assert "TCTD_2024_D100" in {h.clause_code for h in out.hits if h.via_expansion}


class SpyEmbedder:
    """Mock adapter: xác nhận retrieval tính lại embedding (seed là vector giả)."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self._inner = LexicalHashEmbedder()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        return await self._inner.embed(texts)


async def test_retrieval_recomputes_embeddings_via_adapter_and_caches() -> None:
    spy = SpyEmbedder()
    svc = make(embedder=spy)
    await svc.hybrid_search("lãi suất")
    await svc.hybrid_search("lừa đảo")
    assert spy.calls[0] == 1 and spy.calls[1] == len(PROVISIONS) and spy.calls[2] == 1
    assert len(spy.calls) == 3  # node chỉ nhúng 1 lần


async def test_unverified_hits_hide_doc_number_and_date() -> None:
    out = await make().hybrid_search("nghị định sandbox P2P 100 triệu")
    nd = next(h for h in out.hits if h.clause_code == "ND_94_2025")
    assert nd.verified is False and nd.doc_number is None and nd.effective_date is None
    assert nd.note


@pytest.mark.parametrize(
    "q",
    [
        "tư vấn tranh chấp đất đai nhà tôi",
        "chia thừa kế như thế nào",
        "làm sao để bùng nợ app vay",
        "cách trốn nợ hợp pháp",
    ],
)
async def test_ec_ai17_domain_guardrail_refuses(q: str) -> None:
    assert not is_in_scope(q)
    out = await make().hybrid_search(q)
    assert out.refused and out.hits == ()
    assert out.refusal_message and "bùng nợ" not in out.refusal_message.lower()


async def test_ec_ai17_in_scope_query_allowed() -> None:
    assert is_in_scope("app vay thu phí trước có phải lừa đảo không")


class FakeRewriter:
    def __init__(self, text: str) -> None:
        self.text = text

    async def rewrite(self, summaries: Sequence[str]) -> str:
        return self.text


@pytest.mark.parametrize(
    "bad",
    [
        "Đây là hành vi usury nghiêm trọng.",  # EC-AI14: từ cấm
        "Theo Điều 999 Bộ luật bịa đặt thì bạn bị phạt.",  # điều luật ngoài danh sách
        "Theo văn bản 12/2099/NĐ-CP thì bạn phạm tội.",  # số hiệu ngoài danh sách
    ],
)
async def test_ec_ai14_unsafe_llm_rewrite_falls_back_to_deterministic(bad: str) -> None:
    svc = make(rewriter=FakeRewriter(bad))
    exp = await svc.explain_verdict("V_RED_CRIMINAL_USURY")
    assert bad not in exp.plain_vi
    assert "Lãi suất" in exp.plain_vi or "lãi" in exp.plain_vi


async def test_safe_llm_rewrite_is_used() -> None:
    good = "Cho vay lãi quá cao có thể bị coi là vi phạm pháp luật hình sự."
    exp = await make(rewriter=FakeRewriter(good)).explain_verdict("V_RED_CRIMINAL_USURY")
    assert good in exp.plain_vi


async def test_unknown_verdict_returns_no_citations() -> None:
    exp = await make().explain_verdict("V_NOT_A_VERDICT")
    assert exp.citations == () and exp.pending_sources == ()
