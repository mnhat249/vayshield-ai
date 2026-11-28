"""EC-AI26 (review T15): số tiền đọc ra phải ĐÚNG giá trị — round-trip qua bộ phân tích ngược."""

from __future__ import annotations

import random

import pytest

from app.triage.verbalizer import normalize_vi

_D = {
    "không": 0, "một": 1, "mốt": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4,
    "năm": 5, "lăm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
}  # fmt: skip


def _group(words: list[str]) -> int:
    n = 0
    for w in words:
        if w in _D:
            n += _D[w]
        elif w == "mười":
            n += 10
        elif w == "mươi":
            n = n - n % 100 + (n % 100) * 10
        elif w == "trăm":
            n *= 100
        elif w == "linh":
            continue
        else:
            raise ValueError(w)
    return n


def parse_vi_number(spoken: str) -> int:
    total = block = 0
    group: list[str] = []
    for w in spoken.split():
        if w == "nghìn":
            block += _group(group) * 1000
            group = []
        elif w == "triệu":
            block += _group(group) * 10**6
            group = []
        elif w == "tỷ":
            block += _group(group)
            group = []
            total += block * 10**9
            block = 0
        else:
            group.append(w)
    return total + block + _group(group)


def _numbers() -> list[int]:
    rnd = random.Random(20261128)
    fixed = [1, 5, 10, 11, 15, 21, 25, 101, 105, 110, 115, 1000, 1001, 1200, 1500, 12_600, 200_000]
    fixed += [1_200_000, 12_600_000, 1_000_005, 100_000_000, 999_999_999, 2_000_500_000]
    magic = [1_800_000, 1_900_000, 1_800_000_000, 1_900_500, 1_000_000_000, 10_000_000_000]
    return fixed + magic + [rnd.randint(1, 10 ** rnd.randint(2, 13)) for _ in range(280)]


def _fmt(x: int, style: str) -> tuple[str, int]:
    dot, comma = f"{x:,}".replace(",", "."), f"{x:,}"
    return {
        "dot": (dot, x),
        "dot_d": (dot + "đ", x),
        "dot_d_space": (dot + " đ", x),
        "comma_VND": (comma + " VND", x),
        "comma_vnd": (comma + " vnd", x),
        "comma_VNĐ": (comma + "VNĐ", x),
        "dot_VND": (dot + " VND", x),
        "plain_dong": (f"{x} đồng", x),
        "plain_VND": (f"{x} VND", x),
        "plain_d": (f"{x}đ", x),
        "prefix_dong_sign": (f"₫{comma}", x),
        "prefix_VND": (f"VND {comma}", x),
        "space_groups": (f"{x:,}".replace(",", " ") + " đ", x),
        "negative": (f"-{dot}đ", -x),
        "k": (f"{x}k", x * 1000),
        "K": (f"{x}K", x * 1000),
        "tr": (f"{x}tr", x * 10**6),
        "TR": (f"{x} TR", x * 10**6),
    }[style]


STYLES = ["dot", "dot_d", "dot_d_space", "comma_VND", "comma_vnd", "comma_VNĐ", "dot_VND", "k"]
STYLES += ["K", "tr", "TR", "plain_dong", "plain_VND", "plain_d", "prefix_dong_sign"]
STYLES += ["prefix_VND", "space_groups", "negative"]


@pytest.mark.parametrize("style", STYLES)
def test_ec_ai26_money_roundtrip_reads_exact_value(style: str) -> None:
    """>= 300 số x 18 định dạng: không bao giờ đọc sai giá trị (nạn nhân nghe đúng số)."""
    nums = _numbers()
    assert len(nums) >= 300
    for x in nums:
        text, expected = _fmt(x, style)
        if style in {"k", "K", "tr", "TR"} and x >= 1000:
            continue
        spoken = normalize_vi(text).replace("đồng", "").strip()
        if expected < 0:
            assert spoken.startswith("âm "), (text, spoken)
            spoken, expected = spoken.removeprefix("âm "), -expected
        assert parse_vi_number(spoken) == expected, (text, spoken)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,200 VND", "một nghìn hai trăm đồng"),
        ("12,600 VND", "mười hai nghìn sáu trăm đồng"),
        ("1.200 vnd", "một nghìn hai trăm đồng"),
        ("500K", "năm trăm nghìn"),
        ("500k", "năm trăm nghìn"),
        ("2TR", "hai triệu"),
        ("1tr2", "một triệu hai trăm nghìn"),
        ("2tr05", "hai triệu không trăm năm mươi nghìn"),
        ("1TR25", "một triệu hai trăm năm mươi nghìn"),
        ("đơn vị VND", "đơn vị đồng"),
    ],
)
def test_ec_ai26_review_major1_vnd_and_units_case_insensitive(text: str, expected: str) -> None:
    assert normalize_vi(text) == expected


@pytest.mark.parametrize("text", ["12k3", "5m", "2tháng", "1x2"])
def test_ec_ai26_ambiguous_unit_is_left_verbatim_not_misread(text: str) -> None:
    assert normalize_vi(text) == text


def test_ec_ai26_decimal_comma_without_currency_stays_decimal() -> None:
    assert normalize_vi("1,5 triệu") == "một phẩy năm triệu"


def test_ec_ai26_huge_amount_over_1e21_reads_with_scale_words_not_digits() -> None:
    assert normalize_vi("1.000.000.000.000.000.000.000.000đ") == "một triệu tỷ tỷ đồng"


def test_ec_ai26_currency_amount_is_never_read_as_hotline_or_phone() -> None:
    assert normalize_vi("1900000 VND") == "một triệu chín trăm nghìn đồng"
    assert normalize_vi("10000000000 VND") == "mười tỷ đồng"
    # Không có tiền tệ: vẫn đọc từng chữ số (SĐT/hotline).
    assert normalize_vi("0912345678").startswith("không chín một hai")
