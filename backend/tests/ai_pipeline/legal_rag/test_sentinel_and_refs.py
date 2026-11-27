"""T13: ngày sentinel của seed không phải ngày hiệu lực thật; không phát tham chiếu ngoài node.

EC-AI16 (hiệu lực), zero legal hallucination.
"""

from __future__ import annotations

import re

from app.core.verdict_codes import VerdictCode

from .test_legal_rag import make, seed_nodes


async def test_sentinel_date_not_treated_as_real_effective_date() -> None:
    """Ngày 1970-01-01 chưa xác minh: vẫn giải thích nhưng ghi rõ 'chưa rõ ngày hiệu lực'."""
    exp = await make().explain_verdict("V_RED_CRIMINAL_USURY")
    assert exp.citations
    for c in exp.citations:
        assert c.effective_date is None
        assert c.note and "Chưa rõ ngày hiệu lực" in c.note
    assert "chưa rõ ngày hiệu lực" in exp.plain_vi
    out = await make().hybrid_search("lãi suất")
    assert all(h.effective_date is None for h in out.hits)
    # ND_94_2025 có ngày thật theo đặc tả (chỉ chưa xác minh); các node còn lại dùng ngày sentinel.
    sentinel_hits = [h for h in out.hits if h.clause_code != "ND_94_2025"]
    assert sentinel_hits
    assert all(h.note and "Chưa rõ ngày hiệu lực" in h.note for h in sentinel_hits)


async def test_citations_never_contain_unlisted_article_or_doc_numbers() -> None:
    """Mọi 'Điều N'/số hiệu phát ra phải nằm trong node được phép của phán quyết."""
    ref = re.compile(r"\d+/\d{4}(?:/[\w\-]+)?|Điều\s+\d+")
    nodes = seed_nodes()
    for verdict in VerdictCode:
        exp = await make().explain_verdict(verdict.value)
        allowed = " ".join(
            n.searchable_text() for n in nodes if verdict.value in n.applicable_verdicts
        )
        emitted = [exp.plain_vi, *(c.quote for c in exp.citations)]
        for text in emitted:
            for r in ref.findall(text):
                assert r in allowed, (verdict, r)
