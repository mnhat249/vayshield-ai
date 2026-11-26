"""Kiểm thử sinh tự động round-trip cho bộ đọc tiền (EC-AI11, EC-AI09).

Sinh số tiền ngẫu nhiên → viết thành chữ/số theo nhiều kiểu (``1 triệu 200``, ``1tr2``,
``1,2 triệu``, ``một triệu hai``, ``mười củ rưỡi``, không dấu ...) → ghép ngữ cảnh trước/sau
(``một tháng``, ``năm 2024``, ``ba kỳ``, ``ngày 15 tháng 3`` ...) → parse.

Bất biến bắt buộc: kết quả LUÔN là số gốc hoặc ``None`` (mơ hồ → hỏi lại) — KHÔNG BAO GIỜ là
một số sai. Với kiểu viết chuẩn + ngữ cảnh rõ ràng, kết quả phải đúng bằng số gốc.
"""

from __future__ import annotations

import unicodedata

from hypothesis import given, settings
from hypothesis import strategies as st

from ai_pipeline.extraction.currency_slang import normalize_vn_currency_slang

DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
SCALES = [(1_000_000_000, "tỷ"), (1_000_000, "triệu"), (1_000, "nghìn")]

# Ngữ cảnh đứng sau: STRICT = phải đọc đúng số; LOOSE = được phép None (không được sai).
STRICT_SUFFIXES = [
    "",
    " một tháng",
    " 1 tuần",
    " mỗi ngày",
    " mỗi tháng",
    " năm 2024",
    " năm nay",
    " năm ngoái",
    " trong 3 tháng",
    " ngày 15 tháng 3",
    ", phải trả 15 triệu",
    " cho tôi",
]
LOOSE_SUFFIXES = [" hai tuần", " ba kỳ", " hai lần", " mười hôm", " 2 tháng", " năm ngày"]
PREFIXES = ["", "vay ", "góp ", "trả ", "đóng ", "phí "]

SETTINGS = settings(max_examples=200, deadline=None, derandomize=True)


def _unit_after_tens(u: int, rng: int) -> str:
    if u == 1:
        return "mốt"
    if u == 4:
        return "tư" if rng % 2 else "bốn"
    if u == 5:
        return "lăm"
    return DIGITS[u]


def words_below_1000(n: int, *, inner: bool, rng: int = 0) -> str:
    """Đọc số 1..999; ``inner`` = nhóm đứng sau nhóm lớn hơn (đọc đủ "không trăm")."""
    h, t, u = n // 100, (n // 10) % 10, n % 10
    parts: list[str] = []
    if h or inner:
        parts += [DIGITS[h], "trăm"]
    if t == 0:
        if u:
            parts += (["lẻ"] if parts else []) + [DIGITS[u]]
    elif t == 1:
        parts.append("mười")
        if u:
            parts.append("lăm" if u == 5 else DIGITS[u])
    else:
        parts += [DIGITS[t], "mươi"]
        if u:
            parts.append(_unit_after_tens(u, rng))
    return " ".join(parts)


def words_formal(amount: int, rng: int = 0) -> str:
    """Đọc chuẩn (số chia hết cho 1000): "một triệu không trăm lẻ năm nghìn"."""
    parts: list[str] = []
    rest = amount
    for scale, name in SCALES:
        g = rest // scale
        rest %= scale
        if g:
            parts.append(f"{words_below_1000(g, inner=bool(parts) and g < 100, rng=rng)} {name}")
    assert rest == 0
    return " ".join(parts)


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").replace("đ", "d")


def _dotted(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def renderings(amount: int, rng: int) -> list[tuple[str, bool]]:
    """(văn bản, có phải kiểu chuẩn — phải đọc ra đúng số trong ngữ cảnh STRICT)."""
    out: list[tuple[str, bool]] = [
        (f"{_dotted(amount)}đ", True),
        (f"{amount:,} vnđ", True),
        (f"{amount} đồng", True),
    ]
    if amount % 1000 == 0:
        out.append((f"{_dotted(amount // 1000)}k", True))
        out.append((words_formal(amount, rng), True))
        out.append((strip_accents(words_formal(amount, rng)), True))
    m, rem = divmod(amount, 1_000_000)
    if 1 <= m <= 999:
        mw = words_below_1000(m, inner=False, rng=rng)
        if rem == 0:
            out += [(f"{m} triệu", True), (f"{mw} củ", True), (f"{m}tr", True)]
        elif rem % 100_000 == 0:
            r = rem // 100_000
            out += [
                (f"{m} triệu {r}", True),
                (f"{m}tr{r}", True),
                (f"{m} triệu {r}00", True),
                (f"{m},{r} triệu", True),
                (f"{m} củ {r}", True),
                (f"{mw} triệu {DIGITS[r]}", True),
                (f"{mw} chai {DIGITS[r]}", True),
            ]
            if r == 5:
                out += [(f"{mw} củ rưỡi", True), (f"{m} triệu rưỡi", True)]
            if r == 4:
                out.append((f"{mw} triệu tư", True))
            if r == 1:
                out.append((f"{mw} triệu mốt", True))
        elif rem % 10_000 == 0 or rem % 1_000 == 0:
            hundreds = rem // 1_000
            out.append((f"{m} triệu {hundreds} nghìn", True))
            if hundreds >= 100:
                # Viết tắt chỉ đúng nghĩa khi phần sau có 3 chữ số: "1 triệu 250" = 1.250.000.
                # ("95 triệu 1" là 95,1 triệu chứ không phải 95.001.000 — không sinh dạng đó.)
                hw = words_below_1000(hundreds, inner=False, rng=rng)
                out += [
                    (f"{m} triệu {hundreds}", True),
                    (f"{m}tr{hundreds}", True),
                    # Nói tắt "một triệu hai trăm năm mươi": được phép None nếu có lượng từ sau.
                    (f"{mw} triệu {hw}", False),
                ]
    if amount < 1_000_000 and amount % 100_000 == 0:
        x = amount // 100_000
        out += [(f"{DIGITS[x]} xị", True), (f"{x} lít", True)]
    if amount < 1_000_000 and amount % 100_000 == 50_000 and amount > 100_000:
        out.append((f"{DIGITS[amount // 100_000]} xị rưỡi", True))
    return out


def _first_money(text: str) -> tuple[bool, int | None]:
    money = normalize_vn_currency_slang(text).money
    if not money:
        return False, None
    return True, money[0].value_vnd


def _check(amount: int, rng: int) -> None:
    for body, canonical in renderings(amount, rng):
        for prefix in PREFIXES:
            for suffix in [*STRICT_SUFFIXES, *LOOSE_SUFFIXES]:
                text = f"{prefix}{body}{suffix}"
                found, value = _first_money(text)
                assert value in (amount, None), f"SỐ SAI: {text!r} → {value}, đúng là {amount}"
                if canonical and suffix in STRICT_SUFFIXES:
                    assert found and value == amount, f"{text!r} → {value}, cần {amount}"


amounts_general = st.integers(min_value=1, max_value=9_999_999).map(lambda k: k * 1_000)
amounts_million_tenth = st.builds(
    lambda m, r: m * 1_000_000 + r * 100_000,
    st.integers(min_value=1, max_value=999),
    st.integers(min_value=0, max_value=9),
)
amounts_million_hundreds = st.builds(
    lambda m, h: m * 1_000_000 + h * 1_000,
    st.integers(min_value=1, max_value=99),
    st.integers(min_value=1, max_value=999).filter(lambda h: h % 10 == 0),
)
amounts_small = st.integers(min_value=1, max_value=19).map(lambda k: k * 50_000)


@SETTINGS
@given(amounts_general, st.integers(min_value=0, max_value=1))
def test_ec_ai11_roundtrip_general_amounts(amount: int, rng: int) -> None:
    """EC-AI11/EC-AI09: số bất kỳ (bội 1000) viết số/chữ/không dấu → đúng hoặc None."""
    _check(amount, rng)


@SETTINGS
@given(amounts_million_tenth, st.integers(min_value=0, max_value=1))
def test_ec_ai11_roundtrip_million_tenths(amount: int, rng: int) -> None:
    """EC-AI11: "1 triệu 2", "1tr2", "1,2 triệu", "một triệu hai", "mười củ rưỡi"."""
    _check(amount, rng)


@SETTINGS
@given(amounts_million_hundreds, st.integers(min_value=0, max_value=1))
def test_ec_ai11_roundtrip_million_hundreds(amount: int, rng: int) -> None:
    """EC-AI11: "1 triệu 250", "1tr250", "một triệu hai trăm năm mươi"."""
    _check(amount, rng)


@SETTINGS
@given(amounts_small)
def test_ec_ai11_roundtrip_slang_xi(amount: int) -> None:
    """EC-AI11: "hai xị", "3 lít", "hai xị rưỡi"."""
    _check(amount, 0)


def test_roundtrip_helpers_sane() -> None:
    assert words_formal(1_005_000) == "một triệu không trăm lẻ năm nghìn"
    assert words_formal(25_000_000) == "hai mươi lăm triệu"
    assert words_below_1000(115, inner=False) == "một trăm mười lăm"
