"""Kiểm thử sinh tự động round-trip cho bộ đọc tiền (EC-AI11, EC-AI09).

Sinh số tiền ngẫu nhiên → viết thành chữ/số theo nhiều kiểu (``1 triệu 200``, ``1tr2``,
``1,2 triệu``, ``một triệu hai``, ``mười củ rưỡi``, không dấu ...) → ghép ngữ cảnh trước/sau
(``một tháng``, ``năm 2024``, ``ba kỳ``, ``ngày 15 tháng 3`` ...) → parse.

Bất biến bắt buộc: kết quả LUÔN là số gốc hoặc ``None`` (mơ hồ → hỏi lại) — KHÔNG BAO GIỜ là
một số sai. Với kiểu viết chuẩn + ngữ cảnh rõ ràng, kết quả phải đúng bằng số gốc.
"""

from __future__ import annotations

import re
import unicodedata

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from ai_pipeline.extraction.currency_slang import normalize_vn_currency_slang

DIGITS = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]
SCALES = [(1_000_000_000, "tỷ"), (1_000_000, "triệu"), (1_000, "nghìn")]

# Hậu tố là RANH GIỚI rõ (whitelist): mọi kiểu viết chuẩn phải đọc đúng số.
BOUNDARY_SUFFIXES = [
    "",
    " một tháng",
    " 1 tuần",
    " mỗi ngày",
    " mỗi tháng",
    " hàng tháng",
    "/tháng",
    " năm 2024",
    " năm ngoái",
    " ngày 15 tháng 3",
    ", phải trả 15 triệu",
    " sau 14 ngày",
    " trong 3 tháng",
]
# Ngữ cảnh lạ (không phải ranh giới): kiểu viết "đóng" (kết thúc bằng đơn vị/đ/rưỡi) vẫn phải
# đúng; kiểu "mở" ("1 triệu 2 ...") được phép None (mơ hồ → hỏi lại).
CONTEXT_SUFFIXES = [
    # Vòng 4: "N năm nay" sau phần lẻ có thể là "N năm nay" (thời lượng) → mơ hồ với kiểu mở.
    " năm nay",
    " tạm thời",
    " bây giờ",
    " lãi 3%/tháng",
    " lãi 20%/năm",
    " lãi 3 % một tháng",
    " ngày 15",
    " ngày mốt",
    " tháng sau",
    " tuần tới",
    " cho tôi",
    " trả góp",
]
# Số + danh từ đếm / kỳ trần: chỉ yêu cầu đúng-hoặc-None.
COUNT_SUFFIXES = [
    " hai tuần",
    " ba kỳ",
    " ba kì",
    " hai lần",
    " mười hôm",
    " 2 tháng",
    " năm ngày",
    " 3 app",
    " 3 giờ chiều",
    " 3 phút",
    " 3 số",
    " 3 tuổi",
    " 3 ngân hàng",
    " 2 lãi",
    " 2 người",
]
ALL_SUFFIXES = [*BOUNDARY_SUFFIXES, *CONTEXT_SUFFIXES, *COUNT_SUFFIXES]
PREFIXES = ["", "vay ", "góp ", "trả ", "đóng ", "phí "]

SETTINGS = settings(max_examples=25, deadline=None, derandomize=True)


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


Kind = str  # "closed" (kết thúc bằng đơn vị/đ/rưỡi) | "open" (còn phần lẻ trần) | "loose"


def renderings(amount: int, rng: int) -> list[tuple[str, Kind]]:
    """(văn bản, loại) — văn bản có dấu; bản không dấu sinh ở ``_check``."""
    out: list[tuple[str, Kind]] = [
        (f"{_dotted(amount)}đ", "closed"),
        (f"{amount:,} vnđ", "closed"),
        (f"{amount} đồng", "closed"),
    ]
    if amount % 1000 == 0:
        out.append((f"{_dotted(amount // 1000)}k", "closed"))
        out.append((words_formal(amount, rng), "closed"))
    m, rem = divmod(amount, 1_000_000)
    if 1 <= m <= 999:
        mw = words_below_1000(m, inner=False, rng=rng)
        if rem == 0:
            out += [(f"{m} triệu", "closed"), (f"{mw} củ", "closed"), (f"{m}tr", "closed")]
        elif rem % 100_000 == 0:
            r = rem // 100_000
            out += [
                (f"{m} triệu {r}", "open"),
                (f"{m}tr{r}", "open"),
                (f"{m} triệu {r}00", "open"),
                (f"{m},{r} triệu", "closed"),
                (f"{m} củ {r}", "open"),
                # Vòng 4: "năm" đơn lẻ làm phần lẻ ("một triệu năm") là mơ hồ → loose.
                (f"{mw} triệu {DIGITS[r]}", "loose" if r == 5 else "open"),
                (f"{mw} chai {DIGITS[r]}", "loose" if r == 5 else "open"),
            ]
            if r == 5:
                out += [(f"{mw} củ rưỡi", "closed"), (f"{m} triệu rưỡi", "closed")]
            if r == 4:
                out.append((f"{mw} triệu tư", "open"))
            if r == 1:
                out.append((f"{mw} triệu mốt", "open"))
        elif rem % 1_000 == 0:
            hundreds = rem // 1_000
            out.append((f"{m} triệu {hundreds} nghìn", "closed"))
            if hundreds >= 100:
                # Viết tắt chỉ đúng nghĩa khi phần sau có 3 chữ số: "1 triệu 250" = 1.250.000.
                # ("95 triệu 1" là 95,1 triệu chứ không phải 95.001.000 — không sinh dạng đó.)
                hw = words_below_1000(hundreds, inner=False, rng=rng)
                out += [
                    (f"{m} triệu {hundreds}", "open"),
                    (f"{m}tr{hundreds}", "open"),
                    (f"{mw} triệu {hw}", "loose"),  # nói tắt "một triệu hai trăm năm mươi"
                ]
    if amount < 1_000_000 and amount % 100_000 == 0:
        x = amount // 100_000
        out += [(f"{DIGITS[x]} xị", "closed"), (f"{x} lít", "closed")]
    if amount < 1_000_000 and amount % 100_000 == 50_000 and amount > 100_000:
        out.append((f"{DIGITS[amount // 100_000]} xị rưỡi", "closed"))
    return out


def _first_money(text: str) -> tuple[bool, int | None]:
    money = normalize_vn_currency_slang(text).money
    if not money:
        return False, None
    return True, money[0].value_vnd


def _assert_ok(text: str, amount: int, *, strict: bool) -> None:
    found, value = _first_money(text)
    assert value in (amount, None), f"SỐ SAI: {text!r} → {value}, đúng là {amount}"
    if strict:
        assert found and value == amount, f"{text!r} → {value}, cần {amount}"


def _check(amount: int, rng: int) -> None:
    for body, kind in renderings(amount, rng):
        for prefix in PREFIXES:
            for suffix in ALL_SUFFIXES:
                # "… năm" + "sau 14 ngày": "năm sau" (năm tới) hay phần lẻ 5? → None hợp lệ.
                year_clash = body.endswith("năm") and suffix.startswith(" sau")
                strict = (
                    kind != "loose"
                    and not year_clash
                    and (
                        suffix in BOUNDARY_SUFFIXES
                        or (kind == "closed" and suffix in CONTEXT_SUFFIXES)
                    )
                )
                _assert_ok(f"{prefix}{body}{suffix}", amount, strict=strict)
                # Bản KHÔNG DẤU toàn câu: chỉ bắt buộc đúng với hậu tố ranh giới.
                plain = strip_accents(f"{prefix}{body}{suffix}")
                # ("sau" không dấu có thể là chữ số 6: "ba trieu hai sau 14 ngay" → None hợp lệ.)
                # Từ yếu không dấu (sau/tam/bay/nam = sau đó/tạm/bây/Nam) sau đơn vị tiền → mơ hồ
                # theo thiết kế ("mot trieu nam" → None), nên không bắt buộc đúng.
                plain_strict = suffix in BOUNDARY_SUFFIXES and not (
                    set(re.findall(r"\w+", plain)) & {"sau", "tam", "bay", "nam"}
                )
                _assert_ok(plain, amount, strict=strict and plain_strict)


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


TAIL_VOCAB = [
    "một", "hai", "ba", "năm", "mười", "mốt", "tư", "trăm", "mươi", "1", "2", "3", "15", "30",
    "200", "2024", "ngày", "tháng", "tuần", "năm", "hôm", "kỳ", "kì", "lần", "mỗi", "hàng",
    "sau", "trong", "tới", "nay", "ngoái", "tạm", "thời", "bây", "giờ", "app", "phút", "tuổi",
    "ngân", "hàng", "lãi", "3%", "phần", "trăm", "trả", "góp", "đ", "đồng", "nghìn", "k",
    "rưỡi", ",", "/", "người", "chiều", "số", "xăng", "nước",
]  # fmt: skip
_NUMBERISH = {"một", "hai", "ba", "năm", "mười", "mốt", "tư", "trăm", "mươi", "rưỡi"}
# "ngân" bỏ dấu = "ngan" = nghìn thật ("1 trieu 1 ngan" = 1.001.000) → không làm từ đầu đuôi.
_UNITS = {"nghìn", "k", "đ", "đồng", ",", "/", "ngân"}
TAIL_HEAD = [
    w
    for w in TAIL_VOCAB
    if (not any(ch.isdigit() for ch in w) or w.endswith("%"))
    and w not in _NUMBERISH
    and w not in _UNITS
]
_ASCII_NUMBERISH = {
    "khong", "mot", "hai", "ba", "bon", "tu", "nam", "lam", "sau", "bay", "tam", "chin",
    "muoi", "tram", "le", "chuc", "ruoi", "nghin", "ngan", "canh", "xi", "lit", "cu", "ty",
    "trieu", "tr", "k", "d", "dong", "chai",
}  # fmt: skip
_CALENDAR_HEADS = {"nay", "ngoái", "sau", "tới", "trước", "kia"}
_PERIOD_HEADS = {"ngày", "tháng", "tuần", "hôm", "kỳ", "kì"}
open_or_closed_amounts = st.one_of(amounts_million_tenth, amounts_million_hundreds, amounts_small)


@settings(max_examples=250, deadline=None, derandomize=True)
@given(
    open_or_closed_amounts,
    st.sampled_from(TAIL_HEAD),
    st.lists(st.sampled_from(TAIL_VOCAB), min_size=0, max_size=4),
    st.booleans(),
)
def test_ec_ai11_fuzz_random_tail_never_wrong(
    amount: int, head: str, tail: list[str], plain: bool
) -> None:
    """EC-AI11 (vòng 3): (số tiền) × (đuôi ngẫu nhiên từ từ vựng mới) — đúng hoặc None,
    không bao giờ ra số sai, dù đuôi vô nghĩa. Đuôi mở đầu bằng một từ KHÔNG phải số (đuôi
    bắt đầu bằng số có thể nối tiếp số tiền một cách hợp lệ: "một triệu một" = 1,1tr)."""
    suffix = " " + " ".join([head, *tail])
    if plain:
        # Bỏ dấu biến từ thường thành từ số THẬT ("tạm ngân" → "tam ngan" = 8 ngàn): văn bản
        # sinh ra mang nghĩa số khác, không dùng để kiểm bất biến.
        assume(not {strip_accents(w) for w in [head, *tail]} & _ASCII_NUMBERISH)
    for body, kind in renderings(amount, 0):
        if kind == "loose":
            continue
        if body.endswith("năm") and head in _CALENDAR_HEADS:
            # "một triệu năm" + "nay" = "… năm nay" → mốc lịch (quyết định review: 1tr, không
            # phải 1,5tr) — văn bản ghép mang nghĩa khác số gốc.
            continue
        if kind == "open" and head in _PERIOD_HEADS:
            # "1 triệu 1" + " ngày" = "1 triệu 1 ngày" → tự nhiên đọc là 1tr/1 ngày: văn bản
            # sinh ra mang nghĩa khác số gốc, không dùng để kiểm bất biến.
            continue
        text = f"vay {body}{suffix}"
        if plain:
            text = strip_accents(text)
        found, value = _first_money(text)
        if not found:
            continue
        # Đuôi có thể tự sinh thêm cụm tiền ("... 2 nghìn"), nhưng cụm ĐẦU luôn là số gốc / None.
        assert value in (amount, None), f"SỐ SAI: {text!r} → {value}, đúng là {amount}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("nhận 4 triệu rưỡi, trả 6 triệu sau 14 ngày", [4_500_000, 6_000_000]),
        ("vay 10 triệu, trả 2 triệu sau 7 ngày", [10_000_000, 2_000_000]),
        ("vay 1 triệu sau 2 tháng trả", [1_000_000]),
        ("vay 1 trieu tam thoi", [None]),
        ("vay 1 trieu bay gio tra", [None]),
        ("vay 1 triệu tạm thời", [1_000_000]),
        ("vay 1 triệu bây giờ trả", [1_000_000]),
        ("vay 2 triệu 3 app", [None]),
        ("vay 2 triệu 3 giờ chiều", [None]),
        ("vay 2 triệu 3 phút", [None]),
        ("vay 2 triệu 3 số", [None]),
        ("2 triệu 3 tuổi", [None]),
        ("2 triệu 3 ngân hàng", [None]),
        ("2 trieu 3 ngan hang", [None]),
        ("2 triệu 2 lãi", [None]),
        ("trả 500 nghìn ba kì", [500_000]),
        ("vay 5 triệu lãi 3%/tháng trong 6 tháng", [5_000_000]),
    ],
)
def test_ec_ai11_round3_reviewer_sentences(text: str, expected: list[int | None]) -> None:
    """EC-AI11 (vòng 3): đúng các câu reviewer báo ra số sai."""
    assert [m.value_vnd for m in normalize_vn_currency_slang(text).money] == expected
