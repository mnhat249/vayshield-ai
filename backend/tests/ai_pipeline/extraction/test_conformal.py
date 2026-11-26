"""``ConformalRouter`` (File 3 §1.2): c_f < 0.82, lệch tổng > 2%, brand conflict, entropy."""

from __future__ import annotations

import math

import pytest

from ai_pipeline.extraction.conformal import (
    ConformalRouter,
    EscalationReason,
    split_conformal_quantile,
    total_mismatch,
)
from ai_pipeline.extraction.gemini_client import TokenCandidate, TokenLogprob
from ai_pipeline.extraction.uncertainty import digit_uncertainties

from ._factory import loan

ROUTER = ConformalRouter()


def _tok(text: str, *probs: tuple[str, float]) -> TokenLogprob:
    top = tuple(TokenCandidate(t, math.log(p)) for t, p in probs)
    return TokenLogprob(text, top[0].logprob if top else 0.0, top)


def _ambiguous_tokens() -> tuple[TokenLogprob, ...]:
    """'8' 0.52 vs '3' 0.44 ở nominal_principal_vnd (EC-AI15)."""
    return (
        _tok('{"nominal_principal_vnd": '),
        _tok("8", ("8", 0.52), ("3", 0.44), ("6", 0.04)),
        _tok("000000", ("000000", 0.999)),
        _tok(', "claimed_lender_name": "App 9"}'),
    )


def test_clean_extraction_not_escalated() -> None:
    d = ROUTER.decide(loan())
    assert not d.escalate
    assert d.reasons == ()


@pytest.mark.parametrize(("conf", "escalate"), [(0.81, True), (0.8199, True), (0.82, False)])
def test_confidence_floor_082(conf: float, escalate: bool) -> None:
    d = ROUTER.decide(loan(critical_fields_min_confidence=conf))
    assert d.escalate is escalate
    assert (EscalationReason.LOW_CONFIDENCE in d.reasons) is escalate


def test_total_mismatch_over_2_percent() -> None:
    """Lịch trả 3×4tr = 12tr; gốc 10tr → ngưỡng 200.000đ."""
    assert not total_mismatch(loan(total_repayment_stated_vnd=12_200_000))
    assert total_mismatch(loan(total_repayment_stated_vnd=12_200_001))
    d = ROUTER.decide(loan(total_repayment_stated_vnd=11_000_000))
    assert EscalationReason.TOTAL_MISMATCH in d.reasons


def test_brand_conflict_flag() -> None:
    d = ROUTER.decide(loan(distinct_loan_brands_detected=["Tia Chớp", "FE Credit"]))
    assert EscalationReason.BRAND_CONFLICT in d.reasons
    same = ROUTER.decide(loan(distinct_loan_brands_detected=["FE Credit", "fe-credit"]))
    assert EscalationReason.BRAND_CONFLICT not in same.reasons


def test_ec_ai15_self_reported_095_but_digit_covered_still_escalates() -> None:
    """EC-AI15: LLM tự khai 0.95 nhưng top-1/top-2 chữ số sát nhau (0.52 vs 0.44)
    → hạ < 0.55, leo thang và ép người dùng xác nhận tay."""
    e = loan(critical_fields_min_confidence=0.95)
    unc = digit_uncertainties(_ambiguous_tokens())
    assert unc[0].field == "nominal_principal_vnd"
    d = ROUTER.decide(e, uncertainties=unc)
    assert d.escalate
    assert EscalationReason.AMBIGUOUS_DIGIT in d.reasons
    assert EscalationReason.LOW_CONFIDENCE in d.reasons
    assert d.calibrated_confidence < 0.55
    assert d.needs_user_confirmation


def test_ec_ai15_high_entropy_escalates() -> None:
    tokens = (
        _tok('{"net_disbursed_vnd": '),
        _tok("5", ("5", 0.4), ("6", 0.3), ("8", 0.3)),
    )
    d = ROUTER.decide(loan(), uncertainties=digit_uncertainties(tokens))
    assert EscalationReason.HIGH_TOKEN_ENTROPY in d.reasons
    assert d.max_entropy_bits > 0.9


def test_ec_ai15_digits_inside_strings_ignored() -> None:
    """Chữ số trong chuỗi (tên app "App 9") không phải giá trị số."""
    unc = digit_uncertainties(_ambiguous_tokens())
    assert all(u.token != ', "claimed_lender_name": "App 9"}' for u in unc)


def test_confident_digits_keep_confidence() -> None:
    tokens = (_tok('{"nominal_principal_vnd": '), _tok("10000000", ("10000000", 0.999)))
    d = ROUTER.decide(loan(), uncertainties=digit_uncertainties(tokens))
    assert not d.escalate


def test_ec_ai10_non_loan_not_escalated() -> None:
    d = ROUTER.decide(
        loan(screen_categories=["NON_LOAN_IRRELEVANT"], critical_fields_min_confidence=0.1)
    )
    assert not d.escalate


def test_low_image_quality_and_extra_reasons() -> None:
    d = ROUTER.decide(
        loan(), image_quality=0.2, extra_reasons=[EscalationReason.PROMPT_INJECTION_SUSPECTED]
    )
    assert d.reasons == (
        EscalationReason.LOW_IMAGE_QUALITY,
        EscalationReason.PROMPT_INJECTION_SUSPECTED,
    )


def test_split_conformal_quantile() -> None:
    scores = [i / 100 for i in range(100)]  # 0.00..0.99
    assert split_conformal_quantile(scores, alpha=0.1) == pytest.approx(0.90)
    assert split_conformal_quantile([0.1, 0.2], alpha=0.01) == math.inf
    with pytest.raises(ValueError):
        split_conformal_quantile(scores, alpha=0)
    router = ConformalRouter.calibrated(scores, alpha=0.1)
    d = router.decide(loan(critical_fields_min_confidence=0.85))  # score 0.15 ≤ q̂
    assert EscalationReason.CONFORMAL_SCORE not in d.reasons
    strict = ConformalRouter(q_hat=0.05)
    assert EscalationReason.CONFORMAL_SCORE in strict.decide(loan()).reasons
