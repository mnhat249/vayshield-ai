"""Review vòng 4 (EC-AI11/EC-AI07): câu cố định của reviewer + bản cố định (seed) của
``fuzz.py`` / ``fuzz2.py``.

Quy tắc duy nhất: mọi nhánh mặc định là mơ hồ (``None``); chỉ trả số khi có MỘT cách đọc duy
nhất VÀ token kế tiếp nằm trong whitelist ranh giới. Bất biến fuzz: 0 số sai, 0 thời hạn sai,
0 crash (kể cả voice).
"""

from __future__ import annotations

import random
import unicodedata
from collections.abc import Callable

import pytest

from ai_pipeline.extraction.currency_slang import normalize_vn_currency_slang
from ai_pipeline.extraction.disentangler import brand_key, fee_key
from ai_pipeline.extraction.voice import transcribe_terms_to_extraction


def _values(text: str) -> list[int | None]:
    return [m.value_vnd for m in normalize_vn_currency_slang(text).money]


def _durations(text: str) -> list[int]:
    return [d.days for d in normalize_vn_currency_slang(text).durations]


# --- BLOCKER 1: kỳ trần + mốc ngoài whitelist -----------------------------------------


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("vay 20 triệu 9 trăm hôm qua", [None]),
        ("hôm qua em vay 3 triệu 5 ngày mai phải trả 4 triệu", [None, 4_000_000]),
        ("vay 1 triệu 2 trăm hôm qua", [None]),
        ("vay 3 tỷ 5 ngày mai trả", [None]),
        ("vay 5 triệu 2 tuần rồi", [None]),
        ("vay 5 triệu 2 năm qua", [None]),
        ("vay 78 trieu 8 tram ngay mai tra", [None]),
        ("vay 48 trieu 350 hom qua", [None]),
        ("vay 4 tỷ 7 hôm qua", [None]),
        ("vay 5 triệu 2 hôm đó", [None]),
        ("vay 5 triệu 2 ngày lễ", [None]),
        ("vay 5 triệu 2 ngày đầu", [None]),
    ],
)
def test_ec_ai11_round4_bare_period_with_unknown_follower(
    text: str, values: list[int | None]
) -> None:
    """EC-AI11 (vòng 4 blocker 1): "hôm qua", "ngày mai", "tuần rồi", "năm qua", "hôm đó"
    → KHÔNG tách số lẻ thành thời hạn (trước: 20tr + 900 ngày)."""
    assert _values(text) == values
    assert _durations(text) == []


@pytest.mark.parametrize(
    ("text", "values", "durations"),
    [
        ("vay 3 triệu hai hôm trả 4 triệu", [3_000_000, 4_000_000], [2]),
        ("vay 10 triệu 3 tháng lãi 5%", [10_000_000], [90]),
        ("góp 100k một ngày trong 30 ngày", [100_000], [30]),
        ("vay 2 triệu 3 tuần nha", [2_000_000], [21]),
    ],
)
def test_ec_ai11_round4_count_with_whitelisted_follower(
    text: str, values: list[int], durations: list[int]
) -> None:
    """Số đếm chỉ khi token sau đơn vị kỳ là ranh giới rõ (hết câu, động từ, trợ từ, lãi...)."""
    assert _values(text) == values
    assert _durations(text) == durations


# --- BLOCKER 2: "N năm sau/trước/nay/rồi/tới" -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "vay 10 triệu 5 năm sau trả 20 triệu",
        "em vay 5 triệu 2 năm trước, giờ phải trả 9 triệu",
        "nợ 5 triệu hai năm nay rồi",
        "vay 5 triệu 2 năm rồi",
        "vay 5 triệu 2 năm tới",
    ],
)
def test_ec_ai11_round4_relative_year_not_calendar_boundary(text: str) -> None:
    """EC-AI11 (vòng 4 blocker 2): chỉ "năm 2024"/"năm ngoái" là mốc lịch ranh giới."""
    assert _values(text)[0] is None
    assert _durations(text) == []


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("vay 10 triệu năm 2024", 10_000_000),
        ("vay 10 triệu năm ngoái", 10_000_000),
        ("vay 1tr2 năm 2024", 1_200_000),
        ("vay 1 triệu 2 năm ngoái", 1_200_000),
    ],
)
def test_ec_ai11_round4_calendar_anchor_still_boundary(text: str, value: int) -> None:
    assert _values(text) == [value]


def test_ec_ai11_round4_voice_relative_year() -> None:
    e = transcribe_terms_to_extraction("vay 10 triệu 5 năm sau trả 20 triệu").extraction
    assert e.nominal_principal_vnd is None
    assert e.total_repayment_stated_vnd == 20_000_000
    e2 = transcribe_terms_to_extraction(
        "hôm qua em vay 3 triệu 5 ngày mai phải trả 4 triệu"
    ).extraction
    assert e2.nominal_principal_vnd is None
    assert e2.tenure_days_total is None


# --- MAJOR 3: số thập phân + phần lẻ / rưỡi ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("vay 1,5tr2 trả 2 triệu", [None, 2_000_000]),
        ("vay 0,5 triệu 5", [None]),
        ("1,5 triệu rưỡi", [None]),
        ("2.5 tỷ 3", [None]),
        ("1.5 triệu 200 nghìn", [1_700_000]),
        ("vay 1,5 triệu", [1_500_000]),
    ],
)
def test_ec_ai11_round4_decimal_then_fraction_ambiguous(
    text: str, values: list[int | None]
) -> None:
    """EC-AI11 (vòng 4 major 3): "1,5tr2" (1,7tr?), "1,5 triệu rưỡi" (2tr?) → mơ hồ;
    hợp số "1.5 triệu 200 nghìn" vẫn nhận."""
    assert _values(text) == values


# --- MAJOR 4: khả dụng — trợ từ, giới từ, "lãi N%" ----------------------------------------


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("vay 2 triệu 3 nha", 2_300_000),
        ("nhận về 2 triệu 3 thôi", 2_300_000),
        ("vay 2 củ 3 ạ", 2_300_000),
        ("trả 2 triệu 3 luôn", 2_300_000),
        ("vay 2 triệu 3 nhé", 2_300_000),
        ("vay 2 triệu 3 lãi 3%", 2_300_000),
        ("vay 2 triệu 3 với lãi 3%", 2_300_000),
        ("vay 2 triệu 3 để mua xe", 2_300_000),
        ("vay 2 triệu 3 từ app X", 2_300_000),
        ("vay 2 triệu 3 qua zalo", 2_300_000),
        ("vay 2 trieu 3 nha", 2_300_000),
        ("vay 2 trieu 3 tu app X", 2_300_000),
    ],
)
def test_ec_ai11_round4_particles_prepositions_rate_are_boundaries(text: str, value: int) -> None:
    assert _values(text) == [value]


@pytest.mark.parametrize(
    "text",
    ["vay 2 triệu 3 cho 2 đứa con", "vay 2 triệu 3 lãi", "vay 2 triệu 3 với 2 người"],
)
def test_ec_ai11_round4_preposition_before_number_not_boundary(text: str) -> None:
    assert _values(text)[0] is None


# --- MINOR --------------------------------------------------------------------------------


def test_ec_ai11_round4_month_date_without_day_word() -> None:
    """ "vay 2 triệu 5 tháng 3 năm 2024": số 5 có thể là ngày → mơ hồ (không phải 2,5tr)."""
    assert _values("vay 2 triệu 5 tháng 3 năm 2024") == [None]
    assert _values("vay 2 triệu ngày 5 tháng 3 năm 2024") == [2_000_000]


def test_ec_ai11_round4_trailing_year_word_ambiguous() -> None:
    """ "phí duy trì 2 triệu năm": 2,5tr hay 2tr/năm? → mơ hồ."""
    assert _values("phí duy trì 2 triệu năm") == [None]


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Vay Đông", "Vay Dòng"),
        ("Tiền Ơi", "Tiện Ơi"),
        ("Vay Việt Nam", "Vay"),
        ("Cash VN", "Cash"),
        ("VN Pay", "Pay"),
        ("Vay Co", "Vay"),
    ],
)
def test_ec_ai07_round4_brand_key_keeps_diacritics_and_single_word(a: str, b: str) -> None:
    """EC-AI07 (vòng 4 minor): so khớp có dấu; không rút hậu tố VN/Việt Nam còn 1 từ."""
    assert brand_key(a) != brand_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Tia Chớp", "Tia Chop App"),
        ("Home Credit VN", "Home Credit"),
        ("FE Credit Việt Nam", "fe-credit"),
        ("Công ty TNHH MTV Tài chính FE Credit", "FE Credit"),
        ("Công ty Tài chính Home Credit", "Home Credit"),
    ],
)
def test_ec_ai07_round4_brand_key_aliases_and_legal_affixes(a: str, b: str) -> None:
    assert brand_key(a) == brand_key(b)


def test_round4_fee_key_nit_phi_vs_tien_phi() -> None:
    """NIT được chấp nhận: "Phí" và "Tiền phí" giữ khác nhau (không strip còn mỗi "phí")."""
    assert fee_key("Phí") != fee_key("Tiền phí")


# --- Bản cố định của fuzz.py / fuzz2.py (reviewer) ---------------------------------------

_W = {1: "một", 2: "hai", 3: "ba", 4: "bốn", 5: "năm", 6: "sáu", 7: "bảy", 8: "tám", 9: "chín"}


def _strip(t: str) -> str:
    d = unicodedata.normalize("NFD", t)
    return "".join(c for c in d if unicodedata.category(c) != "Mn").replace("đ", "d")


def _words(n: int) -> str:
    h, r = divmod(n, 100)
    t, u = divmod(r, 10)
    out: list[str] = []
    if h:
        out += [_W[h], "trăm"]
    if t == 0 and u and h:
        out += ["lẻ", _W[u]]
    elif t == 1:
        out += ["mười"] + ([{5: "lăm"}.get(u, _W[u])] if u else [])
    elif t >= 2:
        out += [_W[t], "mươi"] + ([{1: "mốt", 5: "lăm", 4: "tư"}.get(u, _W[u])] if u else [])
    elif u and not h:
        out += [_W[u]]
    return " ".join(out)


def _money(rnd: random.Random) -> tuple[int, str]:
    a = rnd.randint(1, 99)
    b = rnd.randint(1, 9)
    choices = [
        (a * 10**6, f"{a} triệu"),
        (a * 10**6, f"{a}tr"),
        (a * 10**6, f"{a} tr"),
        (a * 10**6, f"{a} củ"),
        (a * 10**6, f"{_words(a)} triệu"),
        (a * 10**6, f"{_words(a)} củ"),
        (a * 10**6 + b * 10**5, f"{a} triệu {b}"),
        (a * 10**6 + b * 10**5, f"{a}tr{b}"),
        (a * 10**6 + b * 10**5, f"{a} triệu {b}00"),
        (a * 10**6 + b * 10**5, f"{a} triệu {b} trăm"),
        (a * 10**6 + b * 10**5, f"{_words(a)} triệu {_words(b)} trăm nghìn"),
        (a * 10**6 + b * 10**5, f"{_words(a)} triệu {_words(b)} trăm"),
        (a * 10**6 + 500000, f"{a} triệu rưỡi"),
        (a * 10**6 + 500000, f"{_words(a)} củ rưỡi"),
        (a * 10**6 + 500000, f"{a} chai rưỡi"),
        (a * 10000, f"{a * 10}k"),
        (a * 10000, f"{a * 10} nghìn"),
        (a * 10000, f"{a * 10} ngàn"),
        (b * 10**5, f"{b} xị"),
        (b * 10**5 + 50000, f"{_words(b)} xị rưỡi"),
        (b * 10**5, f"{b} lít"),
        (a * 10**6, f"{a}.000.000đ"),
        (a * 10**6, f"{a},000,000 vnd"),
        (a * 10**6, f"{a}000000 đồng"),
        (a * 10**6 + b * 10**5, f"{a},{b} triệu"),
        (a * 10**6 + b * 10**5, f"{a}.{b}tr"),
        (b * 10**6 + 4 * 10**5, f"{_words(b)} triệu tư"),
        (b * 10**6 + 10**5, f"{_words(b)} triệu mốt"),
        (a * 10**6 + b * 10**5 + 50000, f"{a} triệu {b}50"),
        (a * 10**6 + b * 10**5, f"{a} triệu {b} trăm ngàn"),
        (a * 10**6 + b * 10**5, f"{a} triệu {b * 100}k"),
    ]
    return rnd.choice(choices)


_FILLER = ["nha", "á", "thôi", "luôn", "đó", "nhé", "ok", "ạ", "hả", "đấy"]
_TEMPLATES: list[tuple[str, Callable[[int, int], list[int]]]] = [
    ("vay {M} trong {d} tháng", lambda d, p: [d * 30]),
    ("góp {M} mỗi tháng trong {d} tháng", lambda d, p: [d * 30]),
    ("app bắt đóng {M} phí hồ sơ, gọi {phone}", lambda d, p: []),
    ("vay {M} lãi {p}% một tháng", lambda d, p: []),
    ("vay {M}, lãi {p}%/tháng, {d} tháng", lambda d, p: [d * 30]),
    ("ngày 15 tháng 3 vay {M}, hạn {p} tuần", lambda d, p: [p * 7]),
    ("stk {stk} techcombank nhận {M}", lambda d, p: []),
    ("mã đơn {code} vay {M} {p} kỳ", lambda d, p: []),
    ("vay {M} {p} app", lambda d, p: []),
    ("vay {M} hôm qua", lambda d, p: []),
    ("vay {M} ngày mai trả", lambda d, p: []),
    ("vay {M} sau 14 ngày trả {M2}", lambda d, p: [14]),
    ("vay {M} năm ngoái giờ nợ {M2}", lambda d, p: []),
    ("vay {M} {f}", lambda d, p: []),
    ("vay {M} {p} lần rồi", lambda d, p: []),
    (
        "chị ơi em vay {M} mà nó trừ {M2} phí bảo hiểm, còn nhận có {M3} thôi, góp mỗi tuần"
        " {M4} trong {d} tuần",
        lambda d, p: [d * 7],
    ),
    ("em mượn {M} hôm 12/5, nó bắt trả {M2} sau 7 ngày, trễ 1 ngày phạt {M3}", lambda d, p: [7, 1]),
    ("vay {M} ngày 2 tháng 3 năm 2024", lambda d, p: []),
    ("vay {M} lúc 9 giờ", lambda d, p: []),
    ("vay {M} {p} người cùng ký", lambda d, p: []),
    ("vay {M} cho {p} đứa con", lambda d, p: []),
    ("vay {M}/ngày", lambda d, p: []),
    ("phí {M} mỗi kỳ, {d} kỳ", lambda d, p: []),
    ("vay {M} trả trong vòng {d} ngày", lambda d, p: [d]),
    ("vay {M} qua zalo {phone}", lambda d, p: []),
    ("vay {M} lần thứ {p}", lambda d, p: []),
    ("tôi vay {M} tháng 3", lambda d, p: []),
    ("vay {M} tháng 12 năm ngoái", lambda d, p: []),
    ("vay {M} rồi sau {p} tháng trả {M2}", lambda d, p: [p * 30]),
    ("vay {M} lãi 0.{p}% ngày", lambda d, p: []),
    ("vay {M} lãi {p} phần trăm một tháng", lambda d, p: []),
    ("vay {M} vào {p}/{d}", lambda d, p: []),
    ("otp {code} vay {M}", lambda d, p: []),
    ("vay {M} tầm {p} tuần gì đó", lambda d, p: [p * 7]),
    ("vay {M} 5 năm sau trả {M2}", lambda d, p: []),
    ("vay {M} {p} năm nay rồi", lambda d, p: []),
]


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_ec_ai11_round4_reviewer_fuzz_never_wrong(seed: int) -> None:
    """Port cố định của reviewer ``fuzz.py`` — 0 số sai, 0 thời hạn sai, 0 crash (cả voice)."""
    rnd = random.Random(seed)
    bad: list[tuple[str, str, object]] = []
    for _ in range(250):
        tpl, durs_fn = rnd.choice(_TEMPLATES)
        d, p = rnd.randint(2, 24), rnd.randint(1, 5)
        ms = [_money(rnd) for _ in range(4)]
        kw = {
            "d": d,
            "p": p,
            "phone": "09" + "".join(rnd.choice("0123456789") for _ in range(8)),
            "stk": "".join(rnd.choice("0123456789") for _ in range(12)),
            "code": str(rnd.randint(10000, 999999)),
            "f": rnd.choice(_FILLER),
        }
        text = tpl.format(M=ms[0][1], M2=ms[1][1], M3=ms[2][1], M4=ms[3][1], **kw)
        if rnd.random() < 0.35:
            text = _strip(text)
        expected = {v for v, _ in ms}
        result = normalize_vn_currency_slang(text)
        for m in result.money:
            if m.value_vnd is not None and m.value_vnd not in expected:
                bad.append(("MONEY", text, m.value_vnd))
        allowed = durs_fn(d, p)
        for dm in result.durations:
            if dm.days not in allowed:
                bad.append(("DUR", text, dm.days))
        transcribe_terms_to_extraction(text)  # không bao giờ crash
    assert bad == []


def _common(rnd: random.Random) -> tuple[str, list[int]]:
    a, b, d = rnd.randint(1, 50), rnd.randint(1, 9), rnd.randint(2, 24)
    wa = _W[a % 9 + 1]
    templates = [
        (f"vay {a} triệu trong {d} tháng", [a * 10**6]),
        (f"góp {a} triệu {b} mỗi tháng", [a * 10**6 + b * 10**5]),
        (f"góp {wa} triệu {_W[b]} mỗi tháng", [(a % 9 + 1) * 10**6 + b * 10**5]),
        (f"mỗi tháng góp {a} triệu {b}", [a * 10**6 + b * 10**5]),
        (f"vay {a} triệu {b}, trả trong {d} tuần", [a * 10**6 + b * 10**5]),
        (f"vay {a} triệu {b} nha", [a * 10**6 + b * 10**5]),
        (f"nhận về {a} triệu {b} thôi", [a * 10**6 + b * 10**5]),
        (f"vay {a} củ {b} ạ", [a * 10**6 + b * 10**5]),
        (f"trả {a} triệu {b} luôn", [a * 10**6 + b * 10**5]),
        (f"vay {a}tr{b} trong {d} ngày", [a * 10**6 + b * 10**5]),
        (f"vay {a} triệu {b} trăm", [a * 10**6 + b * 10**5]),
        (f"vay {a} triệu, lãi {b}% một tháng, trong {d} tháng", [a * 10**6]),
        (f"góp {b * 50}k một ngày trong {d * 5} ngày", [b * 50000]),
        (f"vay {a} triệu {b} 1 tháng", [a * 10**6 + b * 10**5]),
        (f"vay {a} triệu {b} lãi {b}%", [a * 10**6 + b * 10**5]),
        (f"phí hồ sơ {b * 100} nghìn", [b * 100000]),
        (f"vay {a} triệu {b} trả {a + 2} triệu", [a * 10**6 + b * 10**5, (a + 2) * 10**6]),
        (f"vay {a} triệu {b} với lãi {b}%", [a * 10**6 + b * 10**5]),
        (f"vay {a} triệu {b} để mua xe", [a * 10**6 + b * 10**5]),
        (f"vay {a} triệu {b} từ app X", [a * 10**6 + b * 10**5]),
    ]
    return rnd.choice(templates)


@pytest.mark.parametrize("seed", [1, 2])
def test_ec_ai11_round4_reviewer_fuzz2_everyday_sentences(seed: int) -> None:
    """Port cố định của reviewer ``fuzz2.py`` (câu thường ngày): 0 SAI, và ≥ 95% đọc đúng
    (trước vòng 4: 61%). Phần None còn lại là mơ hồ có chủ đích ("năm" đơn lẻ, từ yếu không
    dấu "sau/tam/bay/nam")."""
    rnd = random.Random(seed)
    ok = wrong = 0
    n = 400
    for _ in range(n):
        text, expected = _common(rnd)
        if rnd.random() < 0.3:
            text = _strip(text)
        vals = _values(text)
        if vals == expected:
            ok += 1
        elif not all(v is None or v in expected for v in vals):
            wrong += 1
    assert wrong == 0
    assert ok >= 0.95 * n
