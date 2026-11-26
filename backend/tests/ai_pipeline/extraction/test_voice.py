"""Voice-to-Cashflow (File 3 §1.3): EC-AI11 (tiếng lóng), EC-AI12 (nhầm âm)."""

from __future__ import annotations

import pytest

from ai_pipeline.extraction.schemas import ScreenCategory
from ai_pipeline.extraction.voice import resolve_phonetic_confusions, transcribe_terms_to_extraction


def test_ec_ai11_voice_full_terms() -> None:
    """EC-AI11: vay mười củ rưỡi, thực nhận tám triệu tư, góp hai xị rưỡi/ngày trong ba tuần."""
    draft = transcribe_terms_to_extraction(
        "vay mười củ rưỡi, thực nhận tám triệu tư, góp hai xị rưỡi một ngày, góp ba tuần"
    )
    e = draft.extraction
    assert e.screen_categories == [ScreenCategory.VOICE_DICTATED_TERMS]
    assert e.nominal_principal_vnd == 10_500_000
    assert e.net_disbursed_vnd == 8_400_000
    assert e.tenure_days_total == 21
    assert [
        (r.period_count, r.period_interval_days, r.amount_per_period_vnd)
        for r in e.repayment_regimes
    ] == [(21, 1, 250_000)]
    assert draft.confirmation.repayment_total_vnd == 21 * 250_000
    # Lời nói luôn phải xác nhận tay (độ tin cậy < 0.82).
    assert draft.needs_confirmation
    assert e.critical_fields_min_confidence < 0.82
    assert [m.value_vnd for m in draft.slang_tokens] == [10_500_000, 8_400_000, 250_000]


def test_ec_ai11_weekly_installments() -> None:
    """EC-AI11: "mượn tám củ rưỡi, mỗi tuần đóng chín xị rưỡi" trong ba tháng."""
    e = transcribe_terms_to_extraction(
        "mượn tám củ rưỡi, mỗi tuần đóng chín xị rưỡi, trong ba tháng"
    ).extraction
    assert e.nominal_principal_vnd == 8_500_000
    assert e.tenure_days_total == 90
    assert e.repayment_regimes[0].amount_per_period_vnd == 950_000
    assert e.repayment_regimes[0].period_interval_days == 7


def test_ec_ai11_upfront_fee_by_voice() -> None:
    e = transcribe_terms_to_extraction(
        "bắt nộp trước phí bảo hiểm hai củ trước khi giải ngân, vay mười củ"
    ).extraction
    assert e.upfront_fee_demand.demanded
    assert e.upfront_fee_demand.amount_vnd == 2_000_000
    assert e.nominal_principal_vnd == 10_000_000


def test_ec_ai11_missing_tenure_flags_note() -> None:
    e = transcribe_terms_to_extraction("vay mười củ, mỗi tuần đóng chín xị").extraction
    assert e.repayment_regimes == []
    assert e.extraction_notes_vi  # hỏi thêm thời hạn
    assert e.missing_critical_page_suspected


def test_ec_ai12_phonetic_confusion_certain() -> None:
    """EC-AI12: "quỷ khoản vay" → "hủy khoản vay"; "trèo" → "treo"; "đối chứng" → "đối ứng"."""
    text, fixes = resolve_phonetic_confusions("Em quỷ khoản vay, tiền trèo mãi")
    assert "hủy khoản vay" in text
    assert "tiền treo" in text
    assert len(fixes) == 2
    assert all(f.certain for f in fixes)
    text2, _ = resolve_phonetic_confusions("quỷ hợp đồng vay, nộp phí đối chứng")
    assert "hủy hợp đồng vay" in text2
    assert "phí đối ứng" in text2


def test_ec_ai12_ambiguous_sign_asks_user() -> None:
    """EC-AI12: "ký khoản vay" chỉ coi là "hủy" khi ngữ cảnh cả câu là muốn bỏ — và hỏi lại."""
    draft = transcribe_terms_to_extraction("tôi không vay nữa, muốn ký khoản vay")
    assert [f.certain for f in draft.phonetic_corrections] == [False]
    assert any("hủy khoản vay" in n for n in draft.extraction.extraction_notes_vi)


def test_ec_ai12_sign_without_cancel_context_untouched() -> None:
    _, fixes = resolve_phonetic_confusions("tôi đồng ý ký khoản vay mười củ")
    assert fixes == ()


def test_ec_ai11_voice_principal_with_one_month_tenure() -> None:
    """EC-AI11 (review blocker): "vay 10 triệu một tháng, phải trả 12 triệu"."""
    draft = transcribe_terms_to_extraction("vay 10 triệu một tháng, phải trả 12 triệu")
    e = draft.extraction
    assert e.nominal_principal_vnd == 10_000_000
    assert e.total_repayment_stated_vnd == 12_000_000
    assert e.tenure_days_total == 30
    assert e.repayment_regimes == []
    assert draft.confirmation.repayment_total_vnd == 12_000_000


def test_ec_ai11_voice_slang_principal_one_month() -> None:
    """EC-AI11: "vay mười củ một tháng, trả mười hai củ" (ví dụ ghi trong code)."""
    e = transcribe_terms_to_extraction("vay mười củ một tháng, trả mười hai củ").extraction
    assert e.nominal_principal_vnd == 10_000_000
    assert e.total_repayment_stated_vnd == 12_000_000
    assert e.tenure_days_total == 30


def test_ec_ai11_voice_two_weeks_tenure() -> None:
    e = transcribe_terms_to_extraction("vay 3 củ hai tuần, phải trả 4 củ").extraction
    assert e.nominal_principal_vnd == 3_000_000
    assert e.total_repayment_stated_vnd == 4_000_000
    assert e.tenure_days_total == 14


@pytest.mark.parametrize(
    "text", ["tôi gửi khoản vay này vào ngân hàng", "con gửi khoản vay cho mẹ giữ"]
)
def test_ec_ai12_gui_khoan_vay_not_silently_rewritten(text: str) -> None:
    """EC-AI12 (review major): "gửi khoản vay" là câu bình thường → không sửa thành "hủy"."""
    out, fixes = resolve_phonetic_confusions(text)
    assert "hủy" not in out
    assert fixes == ()


def test_ec_ai12_gui_with_cancel_context_only_suggested() -> None:
    draft = transcribe_terms_to_extraction("em không muốn vay nữa, gửi khoản vay đi")
    assert [f.certain for f in draft.phonetic_corrections] == [False]
    assert "hủy" not in draft.transcript_normalized


def test_ec_ai11_fee_keyword_after_amount_wins() -> None:
    """EC-AI11 (review minor): "nộp trước 2 triệu phí mở băng thì mới giải ngân 20 triệu"
    → phí đòi trước = 2tr, gốc = 20tr (trước đây bị đảo ngược)."""
    e = transcribe_terms_to_extraction(
        "nó bảo nộp trước 2 triệu phí mở băng thì mới giải ngân 20 triệu"
    ).extraction
    assert e.nominal_principal_vnd == 20_000_000
    assert e.upfront_fee_demand.demanded
    assert e.upfront_fee_demand.amount_vnd == 2_000_000


def test_ec_ai11_principal_then_fee_same_clause() -> None:
    e = transcribe_terms_to_extraction(
        "vay 20 triệu nhưng nó bắt nộp phí bảo hiểm 2 triệu"
    ).extraction
    assert e.nominal_principal_vnd == 20_000_000
    assert e.upfront_fee_demand.amount_vnd == 2_000_000


def test_ec_ai11_rereview_voice_calendar_year_not_fraction() -> None:
    """EC-AI11 (re-review blocker 2): "vay 10 triệu năm 2024, phải trả 15 triệu"."""
    e = transcribe_terms_to_extraction("tôi vay 10 triệu năm 2024, phải trả 15 triệu").extraction
    assert e.nominal_principal_vnd == 10_000_000
    assert e.total_repayment_stated_vnd == 15_000_000


def test_ec_ai11_rereview_voice_installment_one_month() -> None:
    """EC-AI11 (re-review blocker 1): "góp một triệu hai một tháng" = 1,2tr mỗi tháng; chưa có
    thời hạn → không bịa số kỳ, hỏi lại."""
    draft = transcribe_terms_to_extraction("vay 10 triệu, góp một triệu hai một tháng")
    assert draft.extraction.nominal_principal_vnd == 10_000_000
    assert [(m.value_vnd, m.per_period_days) for m in draft.slang_tokens][1] == (1_200_000, 30)
    assert draft.extraction.repayment_regimes == []
    assert any("bao lâu" in n for n in draft.extraction.extraction_notes_vi)
    with_tenure = transcribe_terms_to_extraction(
        "vay 10 triệu, góp một triệu hai mỗi tháng, trong mười tháng"
    ).extraction
    assert [
        (r.period_count, r.period_interval_days, r.amount_per_period_vnd)
        for r in with_tenure.repayment_regimes
    ] == [(10, 30, 1_200_000)]


def test_ec_ai11_rereview_voice_quantifier_not_principal() -> None:
    """ "góp 2 triệu ba kỳ, vay 5 triệu": 2tr KHÔNG phải gốc; hỏi lại ý nghĩa "ba kỳ"."""
    e = transcribe_terms_to_extraction("góp 2 triệu ba kỳ, vay 5 triệu").extraction
    assert e.nominal_principal_vnd == 5_000_000
    assert any("ba kỳ" in n for n in e.extraction_notes_vi)


def test_ec_ai11_voice_ambiguous_amount_asks_again() -> None:
    """Cụm tiền mơ hồ → không gán vào gốc/tổng, ghi chú hỏi lại."""
    draft = transcribe_terms_to_extraction("vay mấy củ, phải trả 3 triệu 30")
    e = draft.extraction
    assert e.nominal_principal_vnd is None
    assert e.total_repayment_stated_vnd is None
    assert sum("Chưa nghe rõ số tiền" in n for n in e.extraction_notes_vi) == 2
    assert draft.needs_confirmation


def test_ec_ai11_round3_voice_percent_not_tenure() -> None:
    """EC-AI11 (vòng 3 blocker 2): "lãi 3%/tháng trong 6 tháng" → thời hạn 180 ngày."""
    e = transcribe_terms_to_extraction("vay 5 triệu lãi 3%/tháng trong 6 tháng").extraction
    assert e.nominal_principal_vnd == 5_000_000
    assert e.tenure_days_total == 180


@pytest.mark.parametrize(
    "text",
    [
        "vay 10 triệu lãi 20%/năm",
        "vay 10 triệu trong 20 năm",
        "mỗi ngày đóng 100k trong 20 năm",
        "vay 0đ",
        "vay 10 triệu trong 3 tháng, sau 2 tháng trả",
        "vay 10 triệu, mỗi ngày đóng 1 triệu trong 9 năm",
    ],
)
def test_ec_ai11_round3_voice_never_crashes(text: str) -> None:
    """Không bao giờ ValidationError (endpoint 500): số lạ → bỏ + ghi chú hỏi lại."""
    draft = transcribe_terms_to_extraction(text)
    e = draft.extraction
    assert e.tenure_days_total is None or 1 <= e.tenure_days_total <= 3650
    assert all(1 <= r.period_count <= 360 for r in e.repayment_regimes)
    assert draft.needs_confirmation


def test_ec_ai11_round3_voice_conflicting_tenures_ask() -> None:
    e = transcribe_terms_to_extraction("vay 10 triệu trong 3 tháng, sau 2 tháng trả").extraction
    assert e.tenure_days_total is None
    assert any("nhiều thời hạn" in n for n in e.extraction_notes_vi)


def test_ec_ai11_round3_voice_date_reference_not_installment() -> None:
    """EC-AI11 (vòng 3 major 5): "đóng 3 triệu ngày 15" KHÔNG ra 90 kỳ × 3tr."""
    e = transcribe_terms_to_extraction(
        "vay 10 triệu trong 3 tháng, đóng 3 triệu ngày 15"
    ).extraction
    assert e.repayment_regimes == []
    assert any("xác nhận" in n for n in e.extraction_notes_vi)
    e2 = transcribe_terms_to_extraction("trả 6 triệu tháng sau").extraction
    assert e2.repayment_regimes == []


def test_ec_ai11_round3_voice_too_many_periods_not_clamped() -> None:
    """Số kỳ > 360 → không kẹp (kẹp sẽ ra lịch trả sai), hỏi lại."""
    e = transcribe_terms_to_extraction("vay 10 triệu, mỗi ngày đóng 100k trong 2 năm").extraction
    assert e.repayment_regimes == []
    assert any("quá lớn" in n for n in e.extraction_notes_vi)
