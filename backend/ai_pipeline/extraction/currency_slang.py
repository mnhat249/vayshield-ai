"""Từ điển số lóng tiền tệ tất định (``Colloquial Currency Normalizer``, File 3 §1.3, EC-AI11).

Quy ước (File 3 EC-AI11): ``củ``/``chai`` = 10^6, ``xị``/``lít`` = 10^5, ``cành``/``k`` = 10^3.
Ví dụ chuẩn: ``"mười củ rưỡi"`` / ``"10 chai rưỡi"`` → 10.500.000; ``"tám triệu tư"`` →
8.400.000; ``"hai xị rưỡi một ngày"`` → 250.000/ngày; ``"năm chục cành"`` → 50.000;
``"góp ba tuần"`` → 21 ngày. Cũng đọc viết tắt hỗn loạn (EC-AI09): ``12tr5``, ``8.500k``,
``10,000,000đ``, ``250d/ngay``.

Hàm thuần, không I/O; kết quả là ``int`` đồng (tính trung gian bằng ``Decimal``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# --- Từ vựng -----------------------------------------------------------------

_DIGIT_WORDS: dict[str, int] = {
    "không": 0,
    "một": 1,
    "mốt": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "tư": 4,
    "năm": 5,
    "lăm": 5,
    "nhăm": 5,
    "sáu": 6,
    "bảy": 7,
    "bẩy": 7,
    "tám": 8,
    "chín": 9,
}
# Không được đứng đầu một cụm số ("tư vấn", "không vay", "lăm le"...).
_NON_INITIAL_DIGITS = frozenset({"không", "mốt", "tư", "lăm", "nhăm"})
_TEN_INITIAL = frozenset({"mười"})
_TENS_SUFFIX = frozenset({"mươi", "chục"})
_HUNDRED = frozenset({"trăm"})
_ODD = frozenset({"lẻ", "linh"})
_HALF = frozenset({"rưỡi", "ruoi"})

# Hệ số nhân tiền tệ: số chuẩn + từ lóng (EC-AI11) + viết tắt không dấu (EC-AI09).
_SCALE_WORDS: dict[str, int] = {
    "nghìn": 1_000,
    "ngàn": 1_000,
    "nghin": 1_000,
    "ngan": 1_000,
    "cành": 1_000,
    "canh": 1_000,
    "k": 1_000,
    "xị": 100_000,
    "xi": 100_000,
    "lít": 100_000,
    "lit": 100_000,
    "triệu": 1_000_000,
    "trieu": 1_000_000,
    "tr": 1_000_000,
    "củ": 1_000_000,
    "cu": 1_000_000,
    "chai": 1_000_000,
    "tỷ": 1_000_000_000,
    "tỉ": 1_000_000_000,
    "ty": 1_000_000_000,
}
_CURRENCY_TERMINAL = frozenset({"đ", "d", "đồng", "dong", "vnd", "vnđ"})

PERIOD_DAYS: dict[str, int] = {
    "ngày": 1,
    "ngay": 1,
    "tuần": 7,
    "tuan": 7,
    "tháng": 30,
    "thang": 30,
}
_YEAR_WORDS = frozenset({"năm", "nam"})
_YEAR_DAYS = 365
_PER_PERIOD_PREFIX = frozenset({"mỗi", "moi", "hàng", "hang", "mọi", "một", "1"})

_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*|[^\W\d_]+", re.UNICODE)
_CLAUSE_SEP_RE = re.compile(r"[,;.!?\n]+(?!\d)")
_NUMERIC_RE = re.compile(r"\d+(?:[.,]\d+)*")


def normalize_text(text: str) -> str:
    """EC-AI23: NFKC + chữ thường trước mọi bước NLP."""
    return unicodedata.normalize("NFKC", text).lower()


@dataclass(frozen=True, slots=True)
class _Token:
    text: str
    start: int
    end: int
    clause: int


@dataclass(frozen=True, slots=True)
class MoneyMention:
    """Một cụm tiền đã chuẩn hóa. ``start``/``end`` là vị trí ký tự trong văn bản đã NFKC."""

    raw: str
    value_vnd: int
    start: int
    end: int
    per_period_days: int | None = None
    # False khi mốc kỳ là "một/1 <kỳ>" (có thể là thời hạn: "vay 10 củ một tháng").
    per_period_explicit: bool = True


@dataclass(frozen=True, slots=True)
class DurationMention:
    raw: str
    days: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class SlangNormalization:
    source_text: str  # văn bản đã NFKC + lower
    normalized_text: str  # cụm tiền thay bằng "<số> đồng"
    money: tuple[MoneyMention, ...]
    durations: tuple[DurationMention, ...]


@dataclass(slots=True)
class _NumParse:
    value: Decimal
    end_index: int  # chỉ số token kế tiếp (exclusive)
    is_money: bool


def _tokenize(text: str) -> list[_Token]:
    separators = [m.start() for m in _CLAUSE_SEP_RE.finditer(text)]
    tokens: list[_Token] = []
    clause = 0
    sep_idx = 0
    for match in _TOKEN_RE.finditer(text):
        while sep_idx < len(separators) and separators[sep_idx] < match.start():
            clause += 1
            sep_idx += 1
        tokens.append(_Token(match.group(0), match.start(), match.end(), clause))
    return tokens


def _parse_numeric(token: str) -> tuple[Decimal, int]:
    """Trả (giá trị, số chữ số nguyên khi không có dấu phân cách — để đọc '12tr5')."""
    parts = re.split(r"[.,]", token)
    if len(parts) == 1:
        return Decimal(token), len(token)
    head, tail = parts[0], parts[1:]
    if all(len(p) == 3 for p in tail) and 1 <= len(head) <= 3:
        return Decimal("".join(parts)), 0  # dấu phân cách hàng nghìn: 10.000 / 10,000,000
    if len(tail) == 1 and len(tail[0]) in (1, 2):
        return Decimal(f"{head}.{tail[0]}"), 0  # thập phân: 1,5 / 12.25
    if all(len(p) == 3 for p in tail[:-1]) and len(tail[-1]) in (1, 2):
        return Decimal("".join([head, *tail[:-1]]) + "." + tail[-1]), 0  # 1.500,5
    return Decimal("".join(parts)), 0


def _parse_number(tokens: list[_Token], i: int) -> _NumParse | None:
    """Đọc một cụm số (chữ số hoặc chữ tiếng Việt) bắt đầu ở token ``i``."""
    first = tokens[i].text
    if not (
        _NUMERIC_RE.fullmatch(first)
        or (first in _DIGIT_WORDS and first not in _NON_INITIAL_DIGITS)
        or first in _TEN_INITIAL
    ):
        return None

    total = Decimal(0)
    group = Decimal(0)
    pending: Decimal | None = None
    pending_digits = 0  # số chữ số của token số đang treo (0 = chữ hoặc có dấu phân cách)
    pending_ctx = "initial"  # initial | after_scale | after_hundred
    prev = "start"
    last_scale = Decimal(1)
    is_money = False
    j = i
    while j < len(tokens):
        tok = tokens[j].text
        if tokens[j].clause != tokens[i].clause:
            break
        if _NUMERIC_RE.fullmatch(tok):
            if pending is None and prev in ("start", "scale"):
                pending, pending_digits = _parse_numeric(tok)
                pending_ctx = "after_scale" if prev == "scale" else "initial"
                prev = "digit"
            else:
                break
        elif tok in _DIGIT_WORDS:
            d = Decimal(_DIGIT_WORDS[tok])
            if prev == "ten" and tok in _YEAR_WORDS:
                break  # "mười năm" = 10 năm (15 là "mười lăm")
            if prev in ("ten", "odd"):
                group += d
                prev = "unit"
            elif pending is None and prev in ("start", "scale", "hundred"):
                if tok in _NON_INITIAL_DIGITS and prev == "start":
                    break
                pending, pending_digits = d, 0
                pending_ctx = {"scale": "after_scale", "hundred": "after_hundred"}.get(
                    prev, "initial"
                )
                prev = "digit"
            else:
                break
        elif tok in _TEN_INITIAL:
            if pending is None and prev in ("start", "scale", "hundred"):
                group += 10
                prev = "ten"
            else:
                break
        elif tok in _TENS_SUFFIX:
            if pending is None:
                break
            group += pending * 10
            pending = None
            prev = "ten"
        elif tok in _HUNDRED:
            if pending is None:
                break
            group += pending * 100
            pending = None
            prev = "hundred"
        elif tok in _ODD:
            if prev != "hundred":
                break
            prev = "odd"
        elif tok in _SCALE_WORDS:
            scale = Decimal(_SCALE_WORDS[tok])
            if pending is not None:
                group += pending
                pending = None
            if group == 0:
                break
            total += group * scale
            group = Decimal(0)
            last_scale = scale
            prev = "scale"
            is_money = True
        elif tok in _HALF:
            if prev == "scale":
                total += last_scale / 2
            elif prev == "hundred":
                group += 50
            else:
                break
            prev = "half"
        elif tok in _CURRENCY_TERMINAL:
            if prev == "start":
                break
            j += 1
            is_money = True
            break
        else:
            break
        j += 1

    if prev == "start":
        return None
    if pending is not None:
        if pending_ctx == "after_scale":
            # "tám triệu tư" = 8,4tr; "12tr5" = 12,5tr; "1 triệu 500" = 1,5tr
            places = pending_digits if pending_digits > 0 else 1
            total += pending * last_scale / (Decimal(10) ** places)
        elif pending_ctx == "after_hundred":
            group += pending * 10  # "một trăm tư" = 140
        else:
            group += pending
    total += group
    return _NumParse(total, j, is_money)


def _to_int(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _period_after(
    tokens: list[_Token], k: int, clause: int
) -> tuple[int | None, int, bool]:
    """Đơn vị kỳ ngay sau cụm tiền: ``... một ngày`` / ``.../ngày`` / ``mỗi tuần``."""
    if k < len(tokens) and tokens[k].clause == clause:
        if tokens[k].text in PERIOD_DAYS:
            return PERIOD_DAYS[tokens[k].text], k + 1, True
        if (
            tokens[k].text in _PER_PERIOD_PREFIX
            and k + 1 < len(tokens)
            and tokens[k + 1].clause == clause
            and tokens[k + 1].text in PERIOD_DAYS
        ):
            explicit = tokens[k].text not in {"một", "1"}
            return PERIOD_DAYS[tokens[k + 1].text], k + 2, explicit
    return None, k, True


def _period_before(tokens: list[_Token], i: int, clause: int) -> int | None:
    """``mỗi tuần đóng chín xị rưỡi``: mốc ``mỗi/hàng + kỳ`` đứng trước trong cùng mệnh đề."""
    lo = max(0, i - 5)
    for k in range(i - 1, lo - 1, -1):
        if tokens[k].clause != clause:
            break
        if (
            tokens[k].text in PERIOD_DAYS
            and k >= 1
            and tokens[k - 1].text in {"mỗi", "moi", "hàng", "hang", "mọi"}
        ):
            return PERIOD_DAYS[tokens[k].text]
    return None


def normalize_vn_currency_slang(text: str) -> SlangNormalization:
    """Tìm và chuẩn hóa mọi cụm tiền / thời hạn trong một câu tiếng Việt bình dân."""
    source = normalize_text(text)
    tokens = _tokenize(source)
    money: list[MoneyMention] = []
    durations: list[DurationMention] = []
    i = 0
    while i < len(tokens):
        parsed = _parse_number(tokens, i)
        if parsed is None:
            i += 1
            continue
        clause = tokens[i].clause
        start = tokens[i].start
        k = parsed.end_index
        if parsed.is_money:
            per_period, k2, explicit = _period_after(tokens, k, clause)
            if per_period is None:
                per_period = _period_before(tokens, i, clause)
                k2 = k
            end = tokens[k2 - 1].end
            money.append(
                MoneyMention(
                    raw=source[start:end],
                    value_vnd=_to_int(parsed.value),
                    start=start,
                    end=end,
                    per_period_days=per_period,
                    per_period_explicit=explicit,
                )
            )
            i = k2
            continue
        # Không có đơn vị tiền → có thể là thời hạn: "ba tuần", "30 ngày", "một năm".
        if k < len(tokens) and tokens[k].clause == clause:
            unit = tokens[k].text
            days_per: int | None = PERIOD_DAYS.get(unit)
            if days_per is None and unit in _YEAR_WORDS:
                days_per = _YEAR_DAYS
            if days_per is not None:
                days = parsed.value * days_per
                end_k = k + 1
                if end_k < len(tokens) and tokens[end_k].text in _HALF:
                    days += Decimal(days_per) / 2
                    end_k += 1
                end = tokens[end_k - 1].end
                durations.append(DurationMention(source[start:end], _to_int(days), start, end))
                i = end_k
                continue
        i = parsed.end_index
    normalized = source
    for m in sorted(money, key=lambda m: m.start, reverse=True):
        normalized = normalized[: m.start] + f"{m.value_vnd} đồng" + normalized[m.end :]
    return SlangNormalization(source, normalized, tuple(money), tuple(durations))


def parse_vnd_amount(text: str) -> int | None:
    """Đọc MỘT cụm tiền (``"10 tr"``, ``"10.000k"``, ``"10,000,000đ"``) → int đồng.

    Trả ``None`` nếu không có cụm tiền nào mang đơn vị; số trần không đơn vị cũng nhận
    nếu toàn bộ chuỗi chỉ là một số (``"10000000"``).
    """
    result = normalize_vn_currency_slang(text)
    if result.money:
        return result.money[0].value_vnd
    stripped = normalize_text(text).strip()
    if _NUMERIC_RE.fullmatch(stripped):
        return _to_int(_parse_numeric(stripped)[0])
    return None
