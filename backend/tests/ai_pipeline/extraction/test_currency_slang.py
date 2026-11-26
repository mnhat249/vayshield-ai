"""Từ điển tiếng lóng tiền tệ (File 3 §1.3, EC-AI11) + viết tắt hỗn loạn (EC-AI09)."""

from __future__ import annotations

import pytest

from ai_pipeline.extraction.currency_slang import normalize_vn_currency_slang, parse_vnd_amount


def _single(text: str) -> tuple[int, int | None]:
    result = normalize_vn_currency_slang(text)
    assert len(result.money) == 1, result.money
    m = result.money[0]
    assert isinstance(m.value_vnd, int)
    return m.value_vnd, m.per_period_days


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mười củ rưỡi", 10_500_000),
        ("10 chai rưỡi", 10_500_000),
        ("tám triệu tư", 8_400_000),
        ("năm chục cành", 50_000),
        ("mười lăm chai", 15_000_000),
        ("tám củ rưỡi", 8_500_000),
        ("3 lít", 300_000),
        ("hai xị", 200_000),
        ("500k", 500_000),
        ("một trăm năm mươi nghìn", 150_000),
        ("hai tỷ", 2_000_000_000),
    ],
)
def test_ec_ai11_slang_units(text: str, expected: int) -> None:
    """EC-AI11: củ/chai = 10^6, xị/lít = 10^5, cành/k = 10^3 → int đồng."""
    value, _ = _single(text)
    assert value == expected


def test_ec_ai11_per_day_rate() -> None:
    """EC-AI11: "hai xị rưỡi một ngày" → 250.000 đồng / kỳ 1 ngày."""
    value, per = _single("hai xị rưỡi một ngày")
    assert value == 250_000
    assert per == 1


def test_ec_ai11_period_before_amount() -> None:
    """EC-AI11: "mượn tám củ rưỡi, mỗi tuần đóng chín xị rưỡi"."""
    result = normalize_vn_currency_slang("mượn tám củ rưỡi, mỗi tuần đóng chín xị rưỡi")
    assert [(m.value_vnd, m.per_period_days) for m in result.money] == [
        (8_500_000, None),
        (950_000, 7),
    ]


@pytest.mark.parametrize(
    ("text", "days"),
    [("góp ba tuần", 21), ("trong 30 ngày", 30), ("vay một năm", 365), ("hai tháng rưỡi", 75)],
)
def test_ec_ai11_durations(text: str, days: int) -> None:
    """EC-AI11: "góp ba tuần" → 21 ngày (không phải tiền)."""
    result = normalize_vn_currency_slang(text)
    assert result.money == ()
    assert [d.days for d in result.durations] == [days]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12tr5", 12_500_000),
        ("8.500k", 8_500_000),
        ("10,000,000đ", 10_000_000),
        ("10.000k", 10_000_000),
        ("10 tr", 10_000_000),
        ("10000000", 10_000_000),
    ],
)
def test_ec_ai09_messy_abbreviations(text: str, expected: int) -> None:
    """EC-AI09: 12tr5, 8.500k, 10,000,000đ ... → int đồng."""
    assert parse_vnd_amount(text) == expected


def test_ec_ai09_literal_small_amount_kept_for_sanity_check() -> None:
    """EC-AI09: "250d/ngay" giữ nguyên nghĩa đen 250 đồng/ngày — bộ kiểm biên độ hỏi lại,
    không tự đoán nhân 1000 khi không có gợi ý đơn vị."""
    value, per = _single("250d/ngay")
    assert value == 250
    assert per == 1


def test_ec_ai11_words_not_numbers_are_ignored() -> None:
    """ "tư vấn", "không vay" không bị đọc thành số."""
    result = normalize_vn_currency_slang("tư vấn không vay, mười củ")
    assert [m.value_vnd for m in result.money] == [10_000_000]


def test_ec_ai11_normalized_text_replaces_mentions() -> None:
    result = normalize_vn_currency_slang("Vay MƯỜI CỦ RƯỠI")
    assert result.normalized_text == "vay 10500000 đồng"


def test_parse_vnd_amount_none_when_no_money() -> None:
    assert parse_vnd_amount("không có số nào") is None


@pytest.mark.parametrize(
    ("text", "money", "durations"),
    [
        ("vay 10 triệu một tháng", [(10_000_000, 30)], []),
        ("đóng 500 nghìn một ngày", [(500_000, 1)], []),
        ("vay 3 củ hai tuần", [(3_000_000, None)], [14]),
        ("vay 5 triệu hai tháng trả 7 triệu", [(5_000_000, None), (7_000_000, None)], [60]),
        ("vay 10 triệu 1 năm", [(10_000_000, None)], [365]),
        ("vay mười củ một năm", [(10_000_000, None)], [365]),
    ],
)
def test_ec_ai11_period_word_after_amount_is_not_fraction(
    text: str, money: list[tuple[int, int | None]], durations: list[int]
) -> None:
    """EC-AI11 (review blocker): "một/hai" trước đơn vị kỳ là thời hạn, không cộng vào tiền.

    Trước: "vay 10 triệu một tháng" → 10.100.000; "vay 3 củ hai tuần" → 3.200.000.
    """
    result = normalize_vn_currency_slang(text)
    assert [(m.value_vnd, m.per_period_days) for m in result.money] == money
    assert [d.days for d in result.durations] == durations


def test_ec_ai11_fraction_after_scale_still_works() -> None:
    """Không hồi quy: "tám triệu tư", "năm củ hai", "1tr2" vẫn là phần lẻ."""
    assert parse_vnd_amount("tám triệu tư") == 8_400_000
    assert parse_vnd_amount("năm củ hai") == 5_200_000
    assert parse_vnd_amount("1tr2") == 1_200_000
    assert parse_vnd_amount("hai triệu năm") == 2_500_000


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("một triệu hai trăm", 1_200_000),
        ("hai triệu năm trăm", 2_500_000),
        ("một tỷ hai trăm", 1_200_000_000),
        ("hai triệu một trăm", 2_100_000),
        ("một triệu hai trăm năm mươi", 1_250_000),
        ("1 triệu 200", 1_200_000),
        ("một triệu hai trăm nghìn", 1_200_000),
        ("năm nghìn hai trăm", 5_200),
        ("một tỷ hai trăm triệu", 1_200_000_000),
    ],
)
def test_ec_ai11_hundreds_after_large_scale(text: str, expected: int) -> None:
    """EC-AI11 (review major): "trăm" sau triệu/tỷ không kèm đơn vị nhỏ = phần của đơn vị lớn,
    khớp với cách viết số "1 triệu 200"."""
    assert parse_vnd_amount(text) == expected
