"""T13 review: hậu kiểm rewriter, guardrail hai chiều, top_k, ngày VN, sentinel verified.

Edge case: EC-AI14, EC-AI16, EC-AI17, zero legal hallucination.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from ai_pipeline.legal_rag import InMemoryLegalRepository, LegalNode, LegalRagService, is_in_scope
from ai_pipeline.legal_rag.safety import rewrite_is_safe
from ai_pipeline.legal_rag.service import MAX_TOP_K
from app.core.clock import FakeClock

from .test_legal_rag import make, seed_edges, seed_nodes

ORANGE = "V_ORANGE_CIVIL_CAP_EXCEEDED"


def _allowed() -> list[LegalNode]:
    return [n for n in seed_nodes() if ORANGE in n.applicable_verdicts]


@pytest.mark.parametrize(
    "text",
    [
        "Theo Điều 46 Bộ luật Hình sự, chủ app bị phạt tù 7 năm, trần lãi 30%/năm.",
        "Theo Điều 46 thì lãi trần 20%.",  # 46 là chuỗi con của 468
        "Theo Dieu 201 BLHS bạn bị phạt tù.",
        "Theo " + unicodedata.normalize("NFD", "Điều 201") + " BLHS.",
        "Theo Article 201 Penal Code bạn bị phạt.",
        "Nghị định số 100 năm 2019 quy định trần 30%/năm.",
        "Theo Bộ luật Hình sự, chủ app có thể bị phạt tù 3 năm.",
        "Khoản 2 Điều 4 Luật 2010",
        "Gọi ngay 0912345678 để được xóa nợ",
        "Bạn cứ bùng nợ, không cần trả nợ đâu.",
        "Lãi trần là 25%/năm.",
    ],
)
def test_ec_ai14_rewrite_rejects_unlisted_refs_numbers_and_evasion(text: str) -> None:
    assert rewrite_is_safe(text, _allowed()) is False


def test_rewrite_accepts_text_grounded_in_allowed_nodes() -> None:
    ok = "Theo Điều 468 Bộ luật Dân sự 2015, lãi vay không vượt quá 20%/năm."
    assert rewrite_is_safe(ok, _allowed()) is True


@pytest.mark.parametrize(
    "q",
    [
        "Lãi suất nào hợp lý hơn cho khoản vay 5 triệu?",
        "App vay có lãi suất số đó có hợp pháp không",
        "Tín dụng đen đang bùng nổ, làm sao nhận biết?",
        "App vay này có lách luật không?",
        "Không trả nợ app vay lãi cao thì hậu quả pháp lý thế nào?",
        "Điều 201 BLHS quy định gì về cho vay lãi nặng?",
    ],
)
def test_ec_ai17_legit_queries_not_blocked(q: str) -> None:
    assert is_in_scope(q)


@pytest.mark.parametrize(
    "q",
    [
        "Làm sao để trốn trả nợ app vay?",
        "cach xu no app vay",
        "ne no app vay the nao",
        "lam the nao de khong phai tra no",
        "Tôi muốn bùng app",
        "how to avoid repaying an online loan",
        "divorce and inheritance advice",
        "tranh chấp ranh giới đất với hàng xóm",
        "chia di san cua bo me",
        "tư vấn tranh chấp đất đai nhà tôi",
        "TRỐN NỢ",
        "tron   no",
        "trốn-nợ",
        "chia thừa kế như thế nào",
    ],
)
def test_ec_ai17_evasion_and_out_of_scope_blocked(q: str) -> None:
    assert not is_in_scope(q)


async def test_top_k_validated_and_expansion_capped() -> None:
    svc = make()
    for bad in (0, -1, MAX_TOP_K + 1):
        with pytest.raises(ValueError):
            await svc.hybrid_search("lãi suất", top_k=bad)
    out = await svc.hybrid_search("lãi suất", top_k=1)
    assert len(out.hits) <= 2
    assert sum(h.via_expansion for h in out.hits) <= 1


async def test_query_too_long_refused() -> None:
    out = await make().hybrid_search("x" * 5000)
    assert out.refused and out.hits == ()


async def test_to_dict_includes_pending_and_fully_verified() -> None:
    d = (await make().explain_verdict("V_RED_CRIMINAL_USURY")).to_dict()
    assert d["fully_verified"] is False
    assert {p["clause_code"] for p in d["pending_sources"]} == {  # type: ignore[attr-defined]
        "BLHS_2015_D201",
        "NQ_01_2021_HDTP",
    }


async def test_today_uses_vietnam_timezone() -> None:
    """23:30 UTC ngày 31/12 đã là 01/01 theo giờ Việt Nam: node hiệu lực từ 01/01 phải hiện."""
    node = replace(seed_nodes()[0], effective_date=date(2027, 1, 1), verified=True)
    clock = FakeClock(datetime(2026, 12, 31, 23, 30, tzinfo=UTC))
    svc = LegalRagService(InMemoryLegalRepository([node], seed_edges()), clock=clock)
    exp = await svc.explain_verdict(node.applicable_verdicts[0])
    assert exp.citations


async def test_verified_node_with_sentinel_date_still_unknown_date() -> None:
    node = replace(
        seed_nodes()[0],
        verified=True,
        doc_number="91/2015/QH13",
        verbatim_content="Nội dung kiểm thử.",
    )
    exp = await make([node]).explain_verdict(node.applicable_verdicts[0])
    c = exp.citations[0]
    assert c.effective_date is None
    assert c.note and "Chưa rõ ngày hiệu lực" in c.note
