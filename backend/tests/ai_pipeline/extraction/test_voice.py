"""Voice-to-Cashflow (File 3 §1.3): EC-AI11 (tiếng lóng), EC-AI12 (nhầm âm)."""

from __future__ import annotations

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
    """EC-AI12: "gửi khoản vay" / "quỷ khoản vay" → "hủy khoản vay"; "trèo" → "treo"."""
    text, fixes = resolve_phonetic_confusions("Em muốn gửi khoản vay, tiền trèo mãi")
    assert "hủy khoản vay" in text
    assert "tiền treo" in text
    assert len(fixes) == 2
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
