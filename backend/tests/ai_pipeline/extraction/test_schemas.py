"""Schema ``MultiImageLoanExtraction`` v2.0 (File 3 §2.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from ai_pipeline.extraction.schemas import (
    SCHEMA_VERSION,
    DeductedFeeItem,
    MultiImageLoanExtraction,
    ScreenCategory,
    SpecialScamMorphology,
    is_non_loan_only,
)

from ._factory import loan


def test_schema_version_and_fields() -> None:
    assert SCHEMA_VERSION == "2.0"
    expected = {
        "screen_categories",
        "distinct_loan_brands_detected",
        "claimed_lender_name",
        "claimed_company_legal_name",
        "is_pawn_asset_secured",
        "is_loan_extension_rollover",
        "special_morphology",
        "nominal_principal_vnd",
        "net_disbursed_vnd",
        "total_repayment_stated_vnd",
        "tenure_days_total",
        "stated_interest_rate_text",
        "deducted_fees_at_source",
        "repayment_regimes",
        "late_penalty_text",
        "upfront_fee_demand",
        "bait_and_switch_detected",
        "missing_critical_page_suspected",
        "critical_fields_min_confidence",
        "extraction_notes_vi",
    }
    assert set(MultiImageLoanExtraction.model_fields) == expected


def test_enum_values_match_spec() -> None:
    assert ScreenCategory.SUSPICIOUS_NOTICE_RECEIPT.value == "SUSPICIOUS_RECEIPT"
    assert {c.value for c in ScreenCategory} >= {
        "MARKETING_OFFER",
        "CONTRACT_TERMS",
        "DISBURSEMENT_SUMMARY",
        "CHAT_CONVERSATION",
        "VOICE_DICTATED_TERMS",
        "NON_LOAN_IRRELEVANT",
    }
    assert SpecialScamMorphology.COMMODITY_RESALE_USURY.value == "COMMODITY_RESALE_USURY"


def test_money_is_int_vnd() -> None:
    """Tiền ở biên là int đồng: 10500000.0 → 10500000; Decimal/chuỗi được nhận."""
    e = loan(nominal_principal_vnd=10_500_000.0, net_disbursed_vnd="8400000")
    assert e.nominal_principal_vnd == 10_500_000
    assert isinstance(e.nominal_principal_vnd, int)
    assert e.net_disbursed_vnd == 8_400_000
    fee = DeductedFeeItem.model_validate({"fee_label": "x", "amount_vnd": Decimal("1200000.4")})
    assert fee.amount_vnd == 1_200_000


@pytest.mark.parametrize(
    "overrides",
    [
        {"nominal_principal_vnd": 0},
        {"net_disbursed_vnd": -1},
        {"critical_fields_min_confidence": 1.5},
        {"tenure_days_total": 0},
        {"nominal_principal_vnd": float("nan")},
        {
            "repayment_regimes": [
                {"period_count": 0, "period_interval_days": 1, "amount_per_period_vnd": 1}
            ]
        },
    ],
)
def test_constraints_enforced(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        loan(**overrides)


def test_upfront_fee_demand_required() -> None:
    with pytest.raises(ValidationError):
        MultiImageLoanExtraction.model_validate(
            {"screen_categories": ["CONTRACT_TERMS"], "critical_fields_min_confidence": 0.9}
        )


def test_ec_ai10_non_loan_only() -> None:
    assert is_non_loan_only(loan(screen_categories=["NON_LOAN_IRRELEVANT"]))
    assert not is_non_loan_only(loan(screen_categories=["NON_LOAN_IRRELEVANT", "CONTRACT_TERMS"]))
