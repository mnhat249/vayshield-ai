"""Thứ tự ưu tiên tường minh khi nhiều luật cùng khớp:
advance-fee > brand spoof > BNPL/straw > criminal usury > civil cap > pawn > yellow > amber > green.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

from app.core.verdict_codes import VERDICT_SEVERITY, Severity, VerdictCode
from app.verdict import (
    RULE_PRIORITY,
    Provenance,
    SpecialMorphology,
    UpfrontFeeDemand,
    VerdictInput,
)
from tests.verdict._helpers import Decide, apr

V = VerdictCode
DEMAND = UpfrontFeeDemand(demanded=True, amount_vnd=2_000_000)


def test_priority_order_is_explicit_and_complete() -> None:
    assert RULE_PRIORITY == (
        V.V_RED_ADVANCE_FEE,
        V.V_RED_BRAND_SPOOF,
        V.V_RED_BNPL_OR_STRAW_SCAM,
        V.V_RED_CRIMINAL_USURY,
        V.V_ORANGE_CIVIL_CAP_EXCEEDED,
        V.V_ORANGE_PAWN_HIGH_FEE,
        V.V_YELLOW_HIGH_COST_LICENSED,
        V.V_AMBER_INCOMPLETE_DOC,
        V.V_GREEN_TRANSPARENT,
    )
    assert set(RULE_PRIORITY) == set(VerdictCode)


def test_priority_matches_severity_order() -> None:
    """Ưu tiên không bao giờ chọn nhãn nhẹ hơn khi nhãn nặng hơn cũng khớp."""
    rank = list(Severity)  # CRITICAL_RED → GREEN theo thứ tự khai báo
    for earlier, later in pairwise(RULE_PRIORITY):
        assert rank.index(VERDICT_SEVERITY[earlier]) <= rank.index(VERDICT_SEVERITY[later])


def test_advance_fee_beats_brand_spoof(decide: Decide) -> None:
    """Mock File 4: mạo danh Home Credit + thu phí mở khóa → ADVANCE_FEE, vẫn kèm hotline thật."""
    inp = VerdictInput(claimed_lender_name="Home Credit", upfront_fee_demand=DEMAND)
    v = decide(inp, apr(0.3), Provenance(beneficiary_is_personal_account=True))
    assert v.verdict_code is V.V_RED_ADVANCE_FEE
    assert v.fired_rules[:2] == (V.V_RED_ADVANCE_FEE, V.V_RED_BRAND_SPOOF)
    assert v.official_hotline_verify == "1900 0001"


def test_brand_spoof_beats_bnpl(decide: Decide) -> None:
    inp = VerdictInput(
        claimed_lender_name="Shinhan Vay Nhanh",
        special_morphology=SpecialMorphology.BNPL_ILLEGAL_CASHOUT,
    )
    v = decide(inp, apr(0.3))
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert V.V_RED_BNPL_OR_STRAW_SCAM in v.fired_rules


def test_bnpl_beats_criminal_usury(decide: Decide) -> None:
    inp = VerdictInput(special_morphology=SpecialMorphology.STRAW_BORROWER_RECRUITMENT)
    v = decide(inp, apr(2.0))
    assert v.verdict_code is V.V_RED_BNPL_OR_STRAW_SCAM
    assert V.V_RED_CRIMINAL_USURY in v.fired_rules


def test_criminal_usury_beats_incomplete_doc(decide: Decide) -> None:
    """File 2 §1.3: cận dưới ≥ 100% là bất biến dù ảnh thiếu trang."""
    inp = VerdictInput(missing_critical_page_suspected=True, distinct_loan_brands_count=2)
    v = decide(inp, apr(1.3, 2.0))
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY
    assert V.V_AMBER_INCOMPLETE_DOC in v.fired_rules


def test_all_red_rules_at_once_yield_advance_fee(decide: Decide) -> None:
    inp = VerdictInput(
        claimed_lender_name="Tài chính HomeCredit Quốc Tế",
        special_morphology=SpecialMorphology.BNPL_ILLEGAL_CASHOUT,
        upfront_fee_demand=DEMAND,
        missing_critical_page_suspected=True,
    )
    v = decide(inp, apr(1.5), Provenance(apk_signature_ok=False))
    assert v.verdict_code is V.V_RED_ADVANCE_FEE
    assert v.fired_rules == (
        V.V_RED_ADVANCE_FEE,
        V.V_RED_BRAND_SPOOF,
        V.V_RED_BNPL_OR_STRAW_SCAM,
        V.V_RED_CRIMINAL_USURY,
        V.V_AMBER_INCOMPLETE_DOC,
    )


def test_fired_rules_sorted_by_priority(decide: Decide) -> None:
    inp = replace(VerdictInput(claimed_lender_name="Home Credit"), distinct_loan_brands_count=3)
    v = decide(inp, apr(0.5), Provenance(apk_signature_ok=True))
    assert v.fired_rules == (V.V_YELLOW_HIGH_COST_LICENSED, V.V_AMBER_INCOMPLETE_DOC)
    assert v.verdict_code is V.V_YELLOW_HIGH_COST_LICENSED
