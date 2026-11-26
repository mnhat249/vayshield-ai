"""``MultiLoanDisentangler`` (File 3 §2.3): EC-AI07, thứ bậc màn hình, S2-EC21, EC-AI24."""

from __future__ import annotations

import pytest

from ai_pipeline.extraction.disentangler import (
    MultiLoanDisentangler,
    PageExtraction,
    brand_key,
    deducted_fee_interval,
    dedupe_fees,
    fee_key,
    merge_pages,
)
from ai_pipeline.extraction.schemas import DeductedFeeItem, ScreenCategory

from ._factory import loan

D = MultiLoanDisentangler()


def test_ec_ai07_two_brands_split_no_cross_merge() -> None:
    """EC-AI07: 2 ảnh Tia Chớp + 2 ảnh FE Credit → 2 thẻ riêng, không ghép số chéo."""
    pages = [
        PageExtraction(0, loan(claimed_lender_name="Tia Chớp", nominal_principal_vnd=5_000_000)),
        PageExtraction(
            1,
            loan(
                claimed_lender_name="FE Credit",
                nominal_principal_vnd=20_000_000,
                total_repayment_stated_vnd=None,
            ),
        ),
        PageExtraction(
            2,
            loan(
                claimed_lender_name="Tia Chop",
                nominal_principal_vnd=None,
                net_disbursed_vnd=3_500_000,
                screen_categories=["DISBURSEMENT_SUMMARY"],
            ),
        ),
        PageExtraction(
            3,
            loan(claimed_lender_name="FE CREDIT", total_repayment_stated_vnd=26_000_000),
        ),
    ]
    result = D.disentangle(pages)
    assert result.requires_loan_selection
    assert len(result.cards) == 2
    by_brand = {c.brand: c for c in result.cards}
    tia, fe = by_brand["Tia Chớp"], by_brand["FE Credit"]
    assert tia.page_indices == (0, 2)
    assert fe.page_indices == (1, 3)
    assert tia.extraction.nominal_principal_vnd == 5_000_000
    assert tia.extraction.net_disbursed_vnd == 3_500_000
    assert fe.extraction.nominal_principal_vnd == 20_000_000
    assert fe.extraction.total_repayment_stated_vnd == 26_000_000
    assert fe.extraction.net_disbursed_vnd == 8_500_000  # của FE, không lấy 3.5tr của Tia Chớp


def test_ec_ai07_single_page_with_two_brands_is_ambiguous() -> None:
    page = PageExtraction(0, loan(distinct_loan_brands_detected=["Tia Chớp", "FE Credit"]))
    result = D.disentangle([page])
    assert result.requires_loan_selection
    assert result.cards[0].ambiguous_multi_brand_page


def test_single_brand_merges_unbranded_pages() -> None:
    pages = [
        PageExtraction(0, loan()),
        PageExtraction(1, loan(claimed_lender_name=None, screen_categories=["CHAT_CONVERSATION"])),
    ]
    result = D.disentangle(pages)
    assert not result.requires_loan_selection
    assert len(result.cards) == 1
    assert result.cards[0].page_indices == (0, 1)


def test_ec_ai10_irrelevant_pages_excluded() -> None:
    pages = [
        PageExtraction(0, loan()),
        PageExtraction(1, loan(screen_categories=["NON_LOAN_IRRELEVANT"], claimed_lender_name="X")),
    ]
    result = D.disentangle(pages)
    assert result.irrelevant_pages == (1,)
    assert result.cards[0].page_indices == (0,)


def test_screen_hierarchy_locks_disbursement_and_flags_bait_and_switch() -> None:
    """File 3 §2.3: quảng cáo "nhận đủ 10tr" nhưng màn giải ngân chỉ 8tr → khóa 8tr + cờ."""
    offer = loan(
        screen_categories=["MARKETING_OFFER"],
        net_disbursed_vnd=10_000_000,
        deducted_fees_at_source=[],
    )
    disb = loan(screen_categories=["DISBURSEMENT_SUMMARY"], net_disbursed_vnd=8_000_000)
    merged, locked, _ = merge_pages([PageExtraction(0, offer), PageExtraction(1, disb)])
    assert merged.net_disbursed_vnd == 8_000_000
    assert locked is ScreenCategory.DISBURSEMENT_SUMMARY
    assert merged.bait_and_switch_detected
    assert merged.screen_categories == [
        ScreenCategory.MARKETING_OFFER,
        ScreenCategory.DISBURSEMENT_SUMMARY,
    ]


def test_hierarchy_chat_outranks_contract_and_flags_never_lowered() -> None:
    contract = loan(missing_critical_page_suspected=True, critical_fields_min_confidence=0.9)
    chat = loan(
        screen_categories=["CHAT_CONVERSATION"],
        net_disbursed_vnd=7_000_000,
        upfront_fee_demand={"demanded": True, "amount_vnd": 2_000_000},
        critical_fields_min_confidence=0.7,
    )
    merged, locked, _ = merge_pages([PageExtraction(0, contract), PageExtraction(1, chat)])
    assert locked is ScreenCategory.CHAT_CONVERSATION
    assert merged.net_disbursed_vnd == 7_000_000
    assert merged.upfront_fee_demand.demanded
    assert merged.missing_critical_page_suspected
    assert merged.critical_fields_min_confidence == 0.7


def test_s2_ec21_overlapping_screenshots_fee_counted_once() -> None:
    """S2-EC21: 3 ảnh của 1 hợp đồng, phí hồ sơ 1.5tr xuất hiện ở 2 ảnh → chỉ tính 1 lần."""
    fee = {"fee_label": "Phí hồ sơ", "amount_vnd": 1_500_000}
    pages = [
        PageExtraction(0, loan(deducted_fees_at_source=[fee])),
        PageExtraction(
            1,
            loan(
                deducted_fees_at_source=[
                    {"fee_label": "phí  HỒ SƠ:", "amount_vnd": 1_500_000},
                    {"fee_label": "Phí bảo hiểm", "amount_vnd": 500_000},
                ]
            ),
        ),
        PageExtraction(2, loan(deducted_fees_at_source=[])),
    ]
    result = D.disentangle(pages)
    card = result.cards[0]
    fees = card.extraction.deducted_fees_at_source
    assert sorted(f.amount_vnd for f in fees) == [500_000, 1_500_000]
    assert card.duplicate_fees_removed == 1
    assert sum(f.amount_vnd for f in fees) == 2_000_000


def test_s2_ec21_two_real_fees_same_label_on_one_page_kept() -> None:
    """Hai khoản cùng nhãn cùng số trên CÙNG một ảnh là hai khoản thật — không gộp."""
    item = DeductedFeeItem(fee_label="Phí dịch vụ", amount_vnd=300_000)
    out, removed = dedupe_fees([[item, item], [item]])
    assert len(out) == 2
    assert removed == 1


def test_ec_ai24_prechecked_insurance_in_worst_case() -> None:
    """EC-AI24: ô bảo hiểm 1.200.000đ tích sẵn mờ → vào worst_case; best_case bỏ được."""
    fees = [
        DeductedFeeItem(fee_label="Phí hồ sơ", amount_vnd=1_000_000),
        DeductedFeeItem(
            fee_label="Mua gói bảo hiểm sức khỏe",
            amount_vnd=1_200_000,
            is_mandatory_or_prechecked=True,
        ),
        DeductedFeeItem(
            fee_label="Gói VIP tùy chọn", amount_vnd=900_000, is_mandatory_or_prechecked=False
        ),
    ]
    interval = deducted_fee_interval(fees)
    assert interval.worst_case_vnd == 2_200_000
    assert interval.best_case_vnd == 1_000_000


def test_ec_ai24_uncertainty_upper_bound_in_worst_case() -> None:
    fees = [DeductedFeeItem(fee_label="Phí", amount_vnd=1_000_000, uncertainty_max_vnd=1_800_000)]
    interval = deducted_fee_interval(fees)
    assert (interval.best_case_vnd, interval.worst_case_vnd) == (1_000_000, 1_800_000)


def test_ec_ai07_brands_differing_by_one_char_not_merged() -> None:
    """EC-AI07 (review major): "App Vay Nhanh A" (10tr) và "App Vay Nhanh B" (3tr) là 2 khoản
    vay khác nhau — so khớp mờ ≥ 90 từng gộp nhầm thành 1 thẻ mang tên A với gốc 3tr."""
    pages = [
        PageExtraction(
            0, loan(claimed_lender_name="App Vay Nhanh A", nominal_principal_vnd=10_000_000)
        ),
        PageExtraction(
            1, loan(claimed_lender_name="App Vay Nhanh B", nominal_principal_vnd=3_000_000)
        ),
    ]
    result = D.disentangle(pages)
    assert result.requires_loan_selection
    assert {c.brand: c.extraction.nominal_principal_vnd for c in result.cards} == {
        "App Vay Nhanh A": 10_000_000,
        "App Vay Nhanh B": 3_000_000,
    }


def test_ec_ai07_brands_differing_by_digit_not_merged() -> None:
    pages = [
        PageExtraction(0, loan(claimed_lender_name="Vay Nhanh 247")),
        PageExtraction(1, loan(claimed_lender_name="Vay Nhanh 248")),
    ]
    assert len(D.disentangle(pages).cards) == 2


def test_ec_ai07_same_brand_spelling_variants_merged() -> None:
    """Cùng thương hiệu khác cách viết (dấu, hoa thường, gạch nối, alias) vẫn là 1 thẻ."""
    pages = [
        PageExtraction(0, loan(claimed_lender_name="FE Credit")),
        PageExtraction(1, loan(claimed_lender_name="fe-credit")),
        PageExtraction(2, loan(claimed_lender_name="FECREDIT VN")),
    ]
    result = D.disentangle(pages)
    assert len(result.cards) == 1
    assert not result.requires_loan_selection
    assert brand_key("Tia Chớp") == brand_key("TIA CHOP")
    assert brand_key("App Vay Nhanh A") != brand_key("App Vay Nhanh B")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Home Credit", "Home Credit VN"),
        ("FE Credit", "FE Credit Việt Nam"),
        ("Tia Chớp", "Tia Chop App"),
        ("Home Credit", "Công ty Tài chính Home Credit"),
        ("FE Credit", "FE CREDIT Finance JSC"),
    ],
)
def test_ec_ai07_rereview_brand_suffixes_same_card(a: str, b: str) -> None:
    """EC-AI07 (re-review major 3): hậu/tiền tố VN/Việt Nam/App/Công ty/Finance/JSC không tách
    cùng một app thành 2 thẻ."""
    pages = [
        PageExtraction(0, loan(claimed_lender_name=a)),
        PageExtraction(1, loan(claimed_lender_name=b, screen_categories=["DISBURSEMENT_SUMMARY"])),
    ]
    result = D.disentangle(pages)
    assert len(result.cards) == 1
    assert not result.requires_loan_selection


def test_ec_ai07_rereview_suffix_strip_keeps_a_b_distinct() -> None:
    assert brand_key("App Vay Nhanh A") != brand_key("App Vay Nhanh B")
    assert brand_key("Vay Nhanh A VN") != brand_key("Vay Nhanh B VN")
    assert brand_key("VN") == "vn"  # bỏ hết thì giữ nguyên, không ra khóa rỗng


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Phí hồ sơ", "Phí hồ sơ (bắt buộc)"),
        ("Phí hồ sơ:", "phí hồ sơ"),
        ("Bảo hiểm khoản vay", "Phí bảo hiểm khoản vay"),
    ],
)
def test_s2_ec21_rereview_fee_label_variants_same_key(a: str, b: str) -> None:
    """S2-EC21 (re-review major 4): nhãn phí khác cách viết là cùng một khoản."""
    assert fee_key(a) == fee_key(b)


def test_s2_ec21_rereview_label_variants_on_one_page_counted_once() -> None:
    fees = [
        DeductedFeeItem(fee_label="Phí hồ sơ:", amount_vnd=1_000_000),
        DeductedFeeItem(fee_label="Phí hồ sơ (bắt buộc)", amount_vnd=1_000_000),
    ]
    out, removed = dedupe_fees([fees])
    assert [f.amount_vnd for f in out] == [1_000_000]
    assert removed == 1
    # Khác số tiền hoặc nhãn khác hẳn → vẫn là 2 khoản.
    assert fee_key("Phí dịch vụ") != fee_key("Phí dịch vụ VIP")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Phí dịch vụ (tháng 1)", "Phí dịch vụ (tháng 2)"),
        ("Phí bảo hiểm [kỳ 1]", "Phí bảo hiểm [kỳ 2]"),
        ("Phí dịch vụ (gói VIP)", "Phí dịch vụ"),
    ],
)
def test_s2_ec21_round3_paren_with_content_keeps_fees_apart(a: str, b: str) -> None:
    """S2-EC21 (vòng 3 major 6): ngoặc có chữ số/tên riêng là một phần nhãn — 2 khoản phí
    cùng số tiền KHÔNG bị gộp (trước: mất 1 khoản phí)."""
    assert fee_key(a) != fee_key(b)
    fees = [
        DeductedFeeItem(fee_label=a, amount_vnd=500_000),
        DeductedFeeItem(fee_label=b, amount_vnd=500_000),
    ]
    out, removed = dedupe_fees([fees])
    assert sum(f.amount_vnd for f in out) == 1_000_000
    assert removed == 0


def test_s2_ec21_round3_filler_paren_and_stacked_prefixes() -> None:
    assert fee_key("Phí hồ sơ (bắt buộc)") == fee_key("Phí hồ sơ")
    assert fee_key("Phí bảo hiểm (đã tích)") == fee_key("bảo hiểm")
    assert fee_key("Tiền phí dịch vụ") == fee_key("Phí dịch vụ")
    assert fee_key("Khoản tiền phí hồ sơ") == fee_key("hồ sơ")


@pytest.mark.parametrize(
    ("a", "b"),
    [("VN Pay", "Pay"), ("Vay Co", "Vay"), ("App Vay Nhanh A", "App Vay Nhanh B")],
)
def test_ec_ai07_round3_brand_noise_only_at_edges(a: str, b: str) -> None:
    """EC-AI07 (vòng 3 minor): chỉ bỏ hậu tố pháp nhân ở cuối và "công ty ..." ở đầu."""
    assert brand_key(a) != brand_key(b)


def test_ec_ai07_round3_legal_prefix_suffix_stripped() -> None:
    assert brand_key("Công ty TNHH MTV Tài chính FE Credit") == brand_key("FE Credit")
    assert brand_key("FE CREDIT Finance JSC") == brand_key("fe credit")
