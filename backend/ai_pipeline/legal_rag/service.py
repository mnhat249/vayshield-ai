"""Verdict-Pinned Legal GraphRAG (File 3 §5.1).

Chiều 1 ``explain_verdict``: chỉ nạp node có ``verdict_code`` trong ``applicable_verdicts``.
Chiều 2 ``hybrid_search``: 0.65 * cosine + 0.35 * BM25, mở rộng 1-hop theo cạnh.
Dữ liệu chưa xác minh (``verified=False``) không bao giờ hiển thị số hiệu/ngày hiệu lực và
không được trích nguyên văn rỗng; tuyệt đối không bịa nội dung điều luật.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta, timezone
from typing import Protocol

from app.core.clock import Clock, SystemClock

from .embedding import Embedder, LexicalHashEmbedder
from .guardrail import MAX_QUERY_CHARS, REFUSAL_MESSAGE, TOO_LONG_MESSAGE, is_in_scope
from .models import (
    W_BM25,
    W_COSINE,
    Citation,
    Explanation,
    LegalNode,
    PendingSource,
    SearchHit,
    SearchOutcome,
)
from .repository import LegalRepository
from .safety import rewrite_is_safe
from .text import bm25_scores, cosine, tokenize

NO_NODE_MESSAGE = "Hiện chưa có quy định còn hiệu lực gắn với phán quyết này trong kho dữ liệu."
MAX_TOP_K = 20
VN_TZ = timezone(timedelta(hours=7))  # Asia/Ho_Chi_Minh, không DST


class PlainRewriter(Protocol):
    """Adapter LLM tùy chọn: diễn đạt lại các ý tóm tắt thành lời đời thường (đã mock được)."""

    async def rewrite(self, summaries: Sequence[str]) -> str: ...


def _snippet(text: str, limit: int = 600) -> str:
    """Cắt tại ranh giới câu, nếu không có thì tại ranh giới từ."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit // 2:
        return cut[: end + 1]
    return cut.rsplit(" ", 1)[0].rstrip(",;: ") + "…"


def _citation(node: LegalNode) -> Citation:
    if node.verified:
        return Citation(
            clause_code=node.clause_code,
            title_vi=node.title_vi,
            quote=node.verbatim_content.strip(),
            verified=True,
            doc_number=node.doc_number,
            effective_date=node.shown_effective_date,
            note=node.status_note(),
        )
    return Citation(
        clause_code=node.clause_code,
        title_vi=node.title_vi,
        quote=node.verbatim_content.strip(),
        verified=False,
        note=node.status_note(),
    )


class LegalRagService:
    def __init__(
        self,
        repo: LegalRepository,
        embedder: Embedder | None = None,
        clock: Clock | None = None,
        rewriter: PlainRewriter | None = None,
    ) -> None:
        self._repo = repo
        self._embedder: Embedder = embedder or LexicalHashEmbedder()
        self._clock: Clock = clock or SystemClock()
        self._rewriter = rewriter
        self._emb_cache: dict[tuple[str, str], list[float]] = {}

    def _today(self) -> date:
        return self._clock.now().astimezone(VN_TZ).date()

    # ---- Chiều 1 ----------------------------------------------------------------------
    async def explain_verdict(self, verdict_code: str) -> Explanation:
        nodes = await self._repo.nodes_for_verdict(verdict_code, self._today())
        # Phòng thủ nhiều lớp: dù repo trả sai, vẫn lọc lại theo danh sách được phép + hiệu lực.
        nodes = [
            n
            for n in nodes
            if verdict_code in n.applicable_verdicts and n.is_in_force(self._today())
        ]
        if not nodes:
            return Explanation(verdict_code=verdict_code, plain_vi=NO_NODE_MESSAGE, citations=())
        citations = tuple(_citation(n) for n in nodes if n.has_verbatim)
        pending = tuple(
            PendingSource(n.clause_code, n.title_vi) for n in nodes if not n.has_verbatim
        )
        all_verified = all(n.verified and n.has_verbatim for n in nodes)
        summaries = [n.plain_summary_vi for n in nodes if n.plain_summary_vi.strip()]

        plain = await self._compose_plain(summaries, nodes)
        if not all_verified:
            plain = (
                "Lưu ý: các nguồn pháp lý dưới đây chưa được đối chiếu với văn bản chính thức, "
                "chỉ mang tính tham khảo"
                + (", chưa rõ ngày hiệu lực. " if any(n.date_unknown for n in nodes) else ". ")
                + plain
            )
        return Explanation(
            verdict_code=verdict_code,
            plain_vi=plain,
            citations=citations,
            pending_sources=pending,
            fully_verified=all_verified,
        )

    async def _compose_plain(self, summaries: list[str], nodes: Sequence[LegalNode]) -> str:
        if self._rewriter is not None and summaries:
            try:
                candidate = (await self._rewriter.rewrite(summaries)).strip()
            except Exception:
                candidate = ""
            if candidate and rewrite_is_safe(candidate, nodes):
                return candidate
        return " ".join(s.strip() for s in summaries)

    # ---- Chiều 2 ----------------------------------------------------------------------
    async def hybrid_search(self, query: str, top_k: int = 5) -> SearchOutcome:
        if not 1 <= top_k <= MAX_TOP_K:
            raise ValueError(f"top_k phải trong [1, {MAX_TOP_K}]")
        if len(query) > MAX_QUERY_CHARS:
            return SearchOutcome(query="", refused=True, refusal_message=TOO_LONG_MESSAGE)
        if not is_in_scope(query):
            return SearchOutcome(query=query, refused=True, refusal_message=REFUSAL_MESSAGE)
        today = self._today()
        nodes = await self._repo.valid_nodes(today)
        if not nodes or not tokenize(query):
            return SearchOutcome(query=query)

        qvec = (await self._embedder.embed([query]))[0]
        vecs = await self._node_vectors(nodes)
        bm = bm25_scores(tokenize(query), [tokenize(n.searchable_text()) for n in nodes])

        scored: list[tuple[float, float, float, LegalNode]] = []
        for n, bm_s in zip(nodes, bm, strict=True):
            cos = cosine(qvec, vecs[n.clause_code])
            scored.append((W_COSINE * cos + W_BM25 * bm_s, cos, bm_s, n))
        scored.sort(key=lambda t: (-t[0], t[3].clause_code))
        top = scored[:top_k]

        hits = [self._hit(n, score, cos, bm_s, False) for score, cos, bm_s, n in top]
        seen = {h.clause_code for h in hits}
        for n in await self._repo.neighbors(seen, today):
            if len(hits) >= 2 * top_k:  # giới hạn số hit mở rộng 1-hop = top_k
                break
            if n.clause_code not in seen and n.is_in_force(today):
                seen.add(n.clause_code)
                hits.append(self._hit(n, 0.0, 0.0, 0.0, True))
        return SearchOutcome(query=query, hits=tuple(hits))

    async def _node_vectors(self, nodes: Sequence[LegalNode]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        missing: list[LegalNode] = []
        for n in nodes:
            key = (n.clause_code, n.searchable_text())
            if key in self._emb_cache:
                out[n.clause_code] = self._emb_cache[key]
            else:
                missing.append(n)
        if missing:
            vectors = await self._embedder.embed([n.searchable_text() for n in missing])
            for n, v in zip(missing, vectors, strict=True):
                self._emb_cache[(n.clause_code, n.searchable_text())] = v
                out[n.clause_code] = v
        return out

    @staticmethod
    def _hit(n: LegalNode, score: float, cos: float, bm_s: float, expansion: bool) -> SearchHit:
        return SearchHit(
            clause_code=n.clause_code,
            title_vi=n.title_vi,
            score=score,
            cosine=cos,
            bm25=bm_s,
            via_expansion=expansion,
            verified=n.verified,
            doc_number=n.doc_number if n.verified else None,
            effective_date=n.shown_effective_date,
            snippet=_snippet(n.verbatim_content.strip()) if n.has_verbatim else "",
            note=n.status_note(),
        )
