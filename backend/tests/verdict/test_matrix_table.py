"""Bảng 9 nhãn File 2 §4.4: mỗi nhãn ≥ 2 test dương + ≥ 1 test biên."""

from __future__ import annotations

import pytest

from app.core.verdict_codes import KernelSignal, Severity, VerdictCode
from app.verdict import (
    AprInterval,
    Contradiction,
    DeductedFee,
    Provenance,
    SpecialMorphology,
    UpfrontFeeDemand,
    VerdictInput,
)
from tests.verdict._helpers import Decide, apr

V = VerdictCode
UNKNOWN = VerdictInput(claimed_lender_name="Vay Tiền Siêu Tốc 24h")
HC = VerdictInput(claimed_lender_name="Home Credit")
VCB = VerdictInput(claimed_lender_name="Vietcombank")
P2P = VerdictInput(claimed_lender_name="Lendzy Demo", principal_vnd=50_000_000)
PAWN = VerdictInput(claimed_lender_name="Cầm Đồ Kimlong Demo", is_pawn_asset_secured=True)
BNPL = VerdictInput(claimed_lender_name="Paylater Zeta")
APK_OK = Provenance(apk_signature_ok=True)


def _with(base: VerdictInput, **changes: object) -> VerdictInput:
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


# --------------------------------------------------------------- V_RED_ADVANCE_FEE ---


def test_advance_fee_positive_unknown_lender_kernel_signal(decide: Decide) -> None:
    inp = _with(
        UNKNOWN,
        net_disbursed_vnd=0,
        upfront_fee_demand=UpfrontFeeDemand(demanded=True, amount_vnd=2_000_000),
    )
    v = decide(inp, AprInterval.advance_fee())
    assert v.verdict_code is V.V_RED_ADVANCE_FEE
    assert v.severity is Severity.CRITICAL_RED
    assert v.kernel_signal is KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM
    assert v.apr_vn_365 is None and v.apr_min is None and v.apr_max is None  # ẩn APR


def test_advance_fee_positive_scam_recovery_morphology(decide: Decide) -> None:
    v = decide(_with(UNKNOWN, special_morphology=SpecialMorphology.SCAM_RECOVERY_FRAUD), apr(0.1))
    assert v.verdict_code is V.V_RED_ADVANCE_FEE
    assert Contradiction.SCAM_RECOVERY_FRAUD in v.contradictions
    assert v.apr_vn_365 is None


def test_advance_fee_positive_amount_unreadable_still_red(decide: Decide) -> None:
    inp = _with(HC, upfront_fee_demand=UpfrontFeeDemand(demanded=True, amount_vnd=None))
    v = decide(inp, AprInterval.advance_fee())
    assert v.verdict_code is V.V_RED_ADVANCE_FEE
    assert v.official_hotline_verify == "1900 0001"


def test_advance_fee_boundary_fee_deducted_at_source_is_not_advance_fee(decide: Decide) -> None:
    """Biên: phí trừ thẳng vào tiền giải ngân (không đòi tiền túi trước) KHÔNG phải Bẫy 1."""
    inp = _with(
        HC,
        net_disbursed_vnd=8_500_000,
        deducted_fees=(DeductedFee(label="Phí hồ sơ", amount_vnd=1_500_000),),
    )
    v = decide(inp, apr(0.30), APK_OK)
    assert v.verdict_code is not V.V_RED_ADVANCE_FEE
    assert v.kernel_signal is None
    assert v.verdict_code is V.V_GREEN_TRANSPARENT


# --------------------------------------------------------------- V_RED_BRAND_SPOOF ---


def test_brand_spoof_positive_lookalike_name_with_independent_signal(decide: Decide) -> None:
    v = decide(
        VerdictInput(claimed_lender_name="Tài chính HomeCredit Quốc Tế"),
        apr(0.30),
        Provenance(beneficiary_is_personal_account=True),
    )
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert v.lookalike_of == "Home Credit"
    assert v.official_hotline_verify == "1900 0001"
    assert Contradiction.LOOKALIKE_BRAND in v.contradictions


def test_brand_spoof_positive_wrong_apk_signature(decide: Decide) -> None:
    v = decide(HC, apr(0.30), Provenance(apk_signature_ok=False))
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert Contradiction.APK_SIGNATURE_MISMATCH in v.contradictions


def test_brand_spoof_positive_personal_beneficiary_account(decide: Decide) -> None:
    """File 2 §1.3 chặng 4: tiêu đề Home Credit nhưng chuyển vào TK cá nhân."""
    v = decide(HC, apr(0.30), Provenance(beneficiary_is_personal_account=True))
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert Contradiction.PERSONAL_BENEFICIARY_ACCOUNT in v.contradictions


def test_brand_spoof_positive_frozen_account_notice(decide: Decide) -> None:
    v = decide(VCB, apr(0.10), Provenance(account_frozen_notice=True))
    assert v.verdict_code is V.V_RED_BRAND_SPOOF


def test_brand_spoof_positive_licensed_name_with_usury_rate_unverified(decide: Decide) -> None:
    v = decide(HC, apr(1.0, 1.5))
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert Contradiction.LICENSED_BRAND_USURIOUS_RATE in v.contradictions


def test_brand_spoof_boundary_rate_just_below_100_or_channel_verified(decide: Decide) -> None:
    """Biên: cận dưới 99,99% hoặc kênh đã xác thực chữ ký → không phải mạo danh."""
    assert decide(HC, apr(0.9999, 1.5)).verdict_code is V.V_AMBER_INCOMPLETE_DOC  # chưa xác thực
    assert decide(HC, apr(1.5), APK_OK).verdict_code is V.V_YELLOW_HIGH_COST_LICENSED


def test_brand_spoof_boundary_unknown_brand_wrong_apk_is_not_spoof(decide: Decide) -> None:
    """Biên: không nêu thương hiệu nào trong registry → sai chữ ký không thành "mạo danh"."""
    v = decide(UNKNOWN, apr(1.2), Provenance(apk_signature_ok=False))
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY


# -------------------------------------------------------- V_RED_BNPL_OR_STRAW_SCAM ---


def test_bnpl_positive_cashout(decide: Decide) -> None:
    inp = _with(UNKNOWN, special_morphology=SpecialMorphology.BNPL_ILLEGAL_CASHOUT)
    v = decide(inp, AprInterval.unavailable("NO_CASHFLOW"))
    assert v.verdict_code is V.V_RED_BNPL_OR_STRAW_SCAM
    assert v.severity is Severity.CRITICAL_RED


def test_straw_positive_even_with_licensed_brand(decide: Decide) -> None:
    inp = _with(HC, special_morphology=SpecialMorphology.STRAW_BORROWER_RECRUITMENT)
    v = decide(inp, apr(0.30))
    assert v.verdict_code is V.V_RED_BNPL_OR_STRAW_SCAM
    assert Contradiction.STRAW_BORROWER_RECRUITMENT in v.contradictions


def test_bnpl_boundary_commodity_resale_is_usury_not_bnpl(decide: Decide) -> None:
    """Biên: mua bán lại cắt phế không phải BNPL/straw — đi nhánh lãi suất (tín dụng đen)."""
    inp = _with(HC, special_morphology=SpecialMorphology.COMMODITY_RESALE_USURY)
    v = decide(inp, apr(1.10), APK_OK)
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY
    assert V.V_RED_BNPL_OR_STRAW_SCAM not in v.fired_rules


# ------------------------------------------------------------ V_RED_CRIMINAL_USURY ---


def test_criminal_usury_positive_daily_installment(decide: Decide) -> None:
    v = decide(UNKNOWN, apr(3.518))
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY
    assert v.severity is Severity.RED
    assert v.apr_vn_365 == 3.518


def test_criminal_usury_positive_lower_bound_exactly_100(decide: Decide) -> None:
    v = decide(UNKNOWN, apr(1.0, 1.4))
    assert v.verdict_code is V.V_RED_CRIMINAL_USURY


def test_criminal_usury_positive_pawn_chain_not_exempt(decide: Decide) -> None:
    assert decide(PAWN, apr(1.5)).verdict_code is V.V_RED_CRIMINAL_USURY


def test_criminal_usury_boundary_interval_crosses_100(decide: Decide) -> None:
    """Biên S2-EC41: [99,9%; 120%] cắt mốc 100% → chưa kết tội, nhắc xác nhận."""
    v = decide(UNKNOWN, apr(0.15, 1.2))
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD in v.contradictions


def test_criminal_usury_boundary_crossing_100_with_lo_above_20_is_at_least_civil(
    decide: Decide,
) -> None:
    """Review A: [50%; 120%] cắt mốc 100% nhưng chắc chắn vượt 20% → CAM ĐẬM + cờ cắt mốc."""
    v = decide(UNKNOWN, apr(0.5, 1.2))
    assert v.verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED
    assert Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD in v.contradictions
    assert decide(UNKNOWN, apr(0.999, 1.2)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


def test_criminal_usury_boundary_point_just_below_100(decide: Decide) -> None:
    assert decide(UNKNOWN, apr(0.9999)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


# ----------------------------------------------------- V_ORANGE_CIVIL_CAP_EXCEEDED ---


def test_civil_cap_positive_unknown_app(decide: Decide) -> None:
    v = decide(UNKNOWN, apr(0.965))
    assert v.verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED
    assert v.severity is Severity.DEEP_ORANGE


def test_civil_cap_positive_just_above_tolerance(decide: Decide) -> None:
    assert decide(UNKNOWN, apr(0.2051)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


def test_civil_cap_positive_p2p_within_cap_above_20(decide: Decide) -> None:
    assert decide(P2P, apr(0.25)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


def test_civil_cap_boundary_tolerance_band(decide: Decide) -> None:
    """Biên S2-EC42: 20,5%/năm vẫn trong dung sai ±0,5 điểm % → không kết luận vượt trần."""
    v = decide(UNKNOWN, apr(0.205))
    assert v.verdict_code is not V.V_ORANGE_CIVIL_CAP_EXCEEDED
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC  # bên cho vay chưa xác minh
    assert Contradiction.LENDER_NOT_VERIFIED in v.contradictions


# ---------------------------------------------------------- V_ORANGE_PAWN_HIGH_FEE ---


def test_pawn_high_fee_positive(decide: Decide) -> None:
    v = decide(PAWN, apr(0.45))
    assert v.verdict_code is V.V_ORANGE_PAWN_HIGH_FEE
    assert v.severity is Severity.ORANGE


def test_pawn_high_fee_positive_near_100(decide: Decide) -> None:
    assert decide(PAWN, apr(0.95)).verdict_code is V.V_ORANGE_PAWN_HIGH_FEE


def test_pawn_high_fee_boundary_civil_tolerance(decide: Decide) -> None:
    """Review C: cầm đồ có APR > 20,5% tối thiểu CAM; trong dung sai 20,5% vẫn xanh."""
    assert decide(PAWN, apr(0.205), APK_OK).verdict_code is V.V_GREEN_TRANSPARENT
    assert decide(PAWN, apr(0.205)).verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert decide(PAWN, apr(0.2051)).verdict_code is V.V_ORANGE_PAWN_HIGH_FEE
    assert decide(PAWN, apr(0.30)).verdict_code is V.V_ORANGE_PAWN_HIGH_FEE
    assert decide(PAWN, apr(0.5, 1.2)).verdict_code is V.V_ORANGE_PAWN_HIGH_FEE
    assert decide(PAWN, apr(0.15, 0.30)).verdict_code is V.V_AMBER_INCOMPLETE_DOC


def test_pawn_boundary_pawn_flag_from_llm_cannot_soften_unregistered_lender(
    decide: Decide,
) -> None:
    """Biên: cờ ``is_pawn_asset_secured`` (LLM) với bên không có trong registry → vẫn CAM ĐẬM."""
    inp = _with(UNKNOWN, is_pawn_asset_secured=True)
    assert decide(inp, apr(0.45)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED
    # Với chuỗi cầm đồ trong registry, cờ LLM không đổi nhánh (registry quyết định).
    no_asset = _with(PAWN, is_pawn_asset_secured=False)
    assert decide(no_asset, apr(0.45)).verdict_code is decide(PAWN, apr(0.45)).verdict_code


# ------------------------------------------------------ V_YELLOW_HIGH_COST_LICENSED ---


def test_yellow_positive_finco_high_apr(decide: Decide) -> None:
    v = decide(HC, apr(0.45), APK_OK)
    assert v.verdict_code is V.V_YELLOW_HIGH_COST_LICENSED
    assert v.severity is Severity.YELLOW_ORANGE


def test_yellow_positive_bank_with_prechecked_insurance(decide: Decide) -> None:
    """EC-AI24: ô bảo hiểm tích sẵn → VÀNG CAM dù APR thấp."""
    inp = _with(
        VCB,
        deducted_fees=(DeductedFee(label="Bảo hiểm sức khỏe", amount_vnd=1_200_000),),
    )
    v = decide(inp, apr(0.15), APK_OK)
    assert v.verdict_code is V.V_YELLOW_HIGH_COST_LICENSED
    assert Contradiction.BUNDLED_INSURANCE in v.contradictions


def test_yellow_positive_verified_channel_above_100(decide: Decide) -> None:
    assert decide(HC, apr(1.2), APK_OK).verdict_code is V.V_YELLOW_HIGH_COST_LICENSED


def test_yellow_boundary_exactly_35(decide: Decide) -> None:
    assert decide(HC, apr(0.35), APK_OK).verdict_code is V.V_GREEN_TRANSPARENT
    assert decide(HC, apr(0.3501), APK_OK).verdict_code is V.V_YELLOW_HIGH_COST_LICENSED


def test_yellow_boundary_unticked_insurance_not_bundled(decide: Decide) -> None:
    inp = _with(
        VCB,
        deducted_fees=(
            DeductedFee(
                label="Bảo hiểm khoản vay", amount_vnd=500_000, is_mandatory_or_prechecked=False
            ),
        ),
    )
    assert decide(inp, apr(0.15), APK_OK).verdict_code is V.V_GREEN_TRANSPARENT


# ---------------------------------------------------------- V_AMBER_INCOMPLETE_DOC ---


def test_amber_positive_apr_unavailable(decide: Decide) -> None:
    v = decide(HC, AprInterval.unavailable("RATE_BELOW_BRACKET"))
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.APR_UNAVAILABLE in v.contradictions


def test_amber_positive_interval_crosses_20(decide: Decide) -> None:
    v = decide(UNKNOWN, apr(0.15, 0.30))
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD in v.contradictions


def test_amber_positive_missing_page_on_otherwise_green(decide: Decide) -> None:
    v = decide(_with(HC, missing_critical_page_suspected=True), apr(0.25), APK_OK)
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.MISSING_CRITICAL_PAGE in v.contradictions


def test_amber_positive_two_distinct_loans_mixed(decide: Decide) -> None:
    v = decide(_with(HC, distinct_loan_brands_count=2), apr(0.25), APK_OK)
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.MULTIPLE_LOANS_MIXED in v.contradictions


def test_amber_boundary_interval_fully_above_cap_is_conclusive(decide: Decide) -> None:
    """Biên S2-EC41: [25%; 30%] không chứa mốc 20% → kết luận được (CAM ĐẬM), không hỏi lại."""
    assert decide(UNKNOWN, apr(0.25, 0.30)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


def test_amber_boundary_missing_page_does_not_soften_heavier_verdict(decide: Decide) -> None:
    v = decide(_with(UNKNOWN, missing_critical_page_suspected=True), apr(0.5))
    assert v.verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED
    assert Contradiction.MISSING_CRITICAL_PAGE in v.contradictions


# ------------------------------------------------------------- V_GREEN_TRANSPARENT ---


def test_green_positive_finco(decide: Decide) -> None:
    v = decide(HC, apr(0.30), APK_OK)
    assert v.verdict_code is V.V_GREEN_TRANSPARENT
    assert v.severity is Severity.GREEN
    assert "lừa đảo" in v.tts_script  # vẫn nhắc bật Khiên Trạm 3


def test_green_positive_p2p_at_cap_exactly(decide: Decide) -> None:
    inp = _with(P2P, principal_vnd=100_000_000)
    v = decide(inp, apr(0.18), APK_OK)
    assert v.verdict_code is V.V_GREEN_TRANSPARENT
    assert "ND_94_2025" in v.legal_basis_ids


def test_green_positive_bnpl_provider(decide: Decide) -> None:
    assert decide(BNPL, apr(0.10), APK_OK).verdict_code is V.V_GREEN_TRANSPARENT


def test_green_boundary_p2p_tolerance_band(decide: Decide) -> None:
    """Biên S2-EC42: P2P 20,5% vẫn xanh; 20,6% → vượt trần."""
    assert decide(P2P, apr(0.205), APK_OK).verdict_code is V.V_GREEN_TRANSPARENT
    assert decide(P2P, apr(0.206)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


def test_green_boundary_unknown_lender_never_green(decide: Decide) -> None:
    assert decide(UNKNOWN, apr(0.05)).verdict_code is V.V_AMBER_INCOMPLETE_DOC


def test_unverified_licensed_brand_screenshot_only_is_at_most_amber(decide: Decide) -> None:
    """Quyết định người dùng: ngân hàng/FinCo chỉ có ảnh chụp (apk_signature_ok=None), tên khớp
    → tối đa AMBER (LENDER_NOT_VERIFIED), không bao giờ XANH / VÀNG CAM."""
    for interval in (apr(0.10), apr(0.30), apr(0.45), apr(0.9)):
        for base in (HC, VCB):
            v = decide(base, interval)
            assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
            assert Contradiction.LENDER_NOT_VERIFIED in v.contradictions
    assert decide(HC, apr(0.30), APK_OK).verdict_code is V.V_GREEN_TRANSPARENT


def test_p2p_over_cap_checked_before_crossing_20(decide: Decide) -> None:
    """Review B: Lendzy 150tr với khoảng [15%; 25%] vẫn CAM ĐẬM (vượt hạn mức), không AMBER."""
    inp = _with(P2P, principal_vnd=150_000_000)
    v = decide(inp, apr(0.15, 0.25))
    assert v.verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED
    assert Contradiction.P2P_SANDBOX_CAP_EXCEEDED in v.contradictions
    assert Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD in v.contradictions


def test_p2p_within_cap_20_to_35_stays_civil_cap_assumption(decide: Decide) -> None:
    """Giả định chờ người duyệt: P2P (không phải TCTD) 20–35% → CAM ĐẬM, không XANH."""
    assert decide(P2P, apr(0.30)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


def test_unverified_pawn_p2p_bnpl_never_green(decide: Decide) -> None:
    """Re-review: quy tắc "chỉ ảnh chụp → tối đa AMBER" áp cả cho cầm đồ / P2P / ví trả sau."""
    for base in (PAWN, P2P, BNPL):
        v = decide(base, apr(0.10))
        assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
        assert Contradiction.LENDER_NOT_VERIFIED in v.contradictions
        assert decide(base, apr(0.10), APK_OK).verdict_code is V.V_GREEN_TRANSPARENT


def test_pawn_without_pledged_asset_is_at_most_amber(decide: Decide) -> None:
    """Re-review: chuỗi cầm đồ nhưng ``is_pawn_asset_secured=False`` → không XANH; nhãn CAM giữ."""
    no_asset = _with(PAWN, is_pawn_asset_secured=False)
    v = decide(no_asset, apr(0.10), APK_OK)
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.PAWN_ASSET_NOT_CONFIRMED in v.contradictions
    assert decide(no_asset, apr(0.45), APK_OK).verdict_code is V.V_ORANGE_PAWN_HIGH_FEE


def test_unverified_licensed_still_flags_bundled_insurance(decide: Decide) -> None:
    """Re-review: nhánh chưa xác thực vẫn ghi BUNDLED_INSURANCE trước khi trả AMBER."""
    inp = _with(VCB, deducted_fees=(DeductedFee(label="Bảo hiểm khoản vay", amount_vnd=900_000),))
    v = decide(inp, apr(0.15))
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.BUNDLED_INSURANCE in v.contradictions
    assert Contradiction.LENDER_NOT_VERIFIED in v.contradictions


@pytest.mark.parametrize(
    "name",
    [
        "Home Credit Official",
        "Home Credit Pro",
        "Home Credit Plus",
        "Home Credit Express",
        "Home Credit Care",
    ],
)
def test_s2_ec16_brand_plus_foreign_suffix_with_personal_account_is_spoof(
    decide: Decide, name: str
) -> None:
    """Re-review BLOCKER (bẫy G02): tên thật + hậu tố lạ, APR 50% + STK cá nhân → MẠO DANH."""
    v = decide(
        VerdictInput(claimed_lender_name=name),
        apr(0.5),
        Provenance(beneficiary_is_personal_account=True),
    )
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert v.lookalike_of == "Home Credit"


def test_brand_spoof_boundary_lookalike_alone_is_only_a_signal(decide: Decide) -> None:
    """Review vòng 3 mục 3: tên nhái KHÔNG kèm tín hiệu độc lập → không ĐỎ mạo danh; chỉ AMBER
    (nhánh lãi suất vẫn áp riêng: 30%/năm với bên không phép → CAM ĐẬM)."""
    inp = VerdictInput(claimed_lender_name="Tài chính HomeCredit Quốc Tế")
    v = decide(inp, apr(0.10))
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.LOOKALIKE_BRAND in v.contradictions
    assert V.V_RED_BRAND_SPOOF not in v.fired_rules
    assert decide(inp, apr(0.30)).verdict_code is V.V_ORANGE_CIVIL_CAP_EXCEEDED


@pytest.mark.parametrize(
    "provenance",
    [
        Provenance(beneficiary_is_personal_account=True),
        Provenance(apk_signature_ok=False),
        Provenance(account_frozen_notice=True),
    ],
)
def test_brand_spoof_lookalike_plus_each_independent_signal(
    decide: Decide, provenance: Provenance
) -> None:
    v = decide(VerdictInput(claimed_lender_name="Home Credit Official"), apr(0.5), provenance)
    assert v.verdict_code is V.V_RED_BRAND_SPOOF


def test_brand_spoof_lookalike_plus_usury_or_domain(decide: Decide) -> None:
    assert (
        decide(VerdictInput(claimed_lender_name="Home Credit Official"), apr(1.2)).verdict_code
        is V.V_RED_BRAND_SPOOF
    )
    inp = VerdictInput(claimed_lender_name="Home Credit Pro", claimed_domain="homecredit-vn.com")
    v = decide(inp, apr(0.1))
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert Contradiction.LOOKALIKE_DOMAIN in v.contradictions


def test_legal_name_lookalike_without_registry_legal_name_never_red(decide: Decide) -> None:
    """Review vòng 3 mục 3: registry thiếu ``official_legal_name`` → token lạ trong tên pháp nhân
    không kéo lên ĐỎ, kể cả có STK cá nhân (vẫn ghi tín hiệu)."""
    from app.core.schemas.common import LegalCategory
    from app.verdict import InMemoryLenderRegistry, LenderRecord, decide_verdict, match_lender

    reg = InMemoryLenderRegistry(
        [
            LenderRecord(
                entity_code="FINCO_HC",
                legal_category=LegalCategory.LICENSED_FINCO,
                official_short_name="Home Credit",
                official_legal_name="",
            )
        ]
    )
    inp = VerdictInput(claimed_company_legal_name="Home Credit Official Vay")
    prov = Provenance(beneficiary_is_personal_account=True)
    m = match_lender(inp, prov, reg)
    v = decide_verdict(inp, apr(0.1), m, prov)
    assert v.verdict_code is not V.V_RED_BRAND_SPOOF
    assert Contradiction.LOOKALIKE_BRAND in v.contradictions
