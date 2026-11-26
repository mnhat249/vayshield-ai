"""Từ điển số lóng tiền tệ tất định (``Colloquial Currency Normalizer``, File 3 §1.3, EC-AI11).

Quy ước (File 3 EC-AI11): ``củ``/``chai`` = 10^6, ``xị``/``lít`` = 10^5, ``cành``/``k`` = 10^3.
Ví dụ chuẩn: ``"mười củ rưỡi"`` / ``"10 chai rưỡi"`` → 10.500.000; ``"tám triệu tư"`` →
8.400.000; ``"hai xị rưỡi một ngày"`` → 250.000/ngày; ``"năm chục cành"`` → 50.000;
``"góp ba tuần"`` → 21 ngày. Cũng đọc viết tắt hỗn loạn (EC-AI09): ``12tr5``, ``8.500k``,
``10,000,000đ``, ``250d/ngay``.

Thiết kế (thay cho vá từng regex):

1. ``_parse_group`` đọc MỘT số < 1000 theo ngữ pháp số đếm tiếng Việt (hoặc một token chữ số).
2. ``_parse_amount`` ghép ``nhóm × đơn vị tiền`` (tỷ > triệu > nghìn ...).
3. Phần đứng SAU một đơn vị tiền (``"1 triệu 200 một tháng"``) đi qua ``_resolve_tail``: liệt kê
   mọi cách đọc hợp lệ (phần lẻ của đơn vị / số đếm của kỳ / hợp số với đơn vị nhỏ hơn).
   Đúng MỘT cách đọc → dùng; 0 hoặc ≥ 2 cách → ``value_vnd = None`` + ``ambiguous=True``
   (KHÔNG đoán — người dùng xác nhận tay). Bộ test round-trip sinh tự động chứng minh
   parser không bao giờ trả về một số SAI (chỉ có số đúng hoặc ``None``).

Hàm thuần, không I/O; kết quả là ``int`` đồng (tính trung gian bằng ``Decimal``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

# --- Từ vựng -----------------------------------------------------------------

_DIGIT_WORDS: dict[str, int] = {
    "không": 0,
    "khong": 0,
    "một": 1,
    "mot": 1,
    "mốt": 1,
    "hai": 2,
    "ba": 3,
    "bốn": 4,
    "bon": 4,
    "tư": 4,
    "tu": 4,
    "năm": 5,
    "nam": 5,
    "lăm": 5,
    "lam": 5,
    "nhăm": 5,
    "sáu": 6,
    "sau": 6,
    "bảy": 7,
    "bẩy": 7,
    "bay": 7,
    "tám": 8,
    "tam": 8,
    "chín": 9,
    "chin": 9,
}
# Không được đứng đầu một số ("tư vấn", "không vay", "lăm le", "từ" viết không dấu ...).
_NON_INITIAL = frozenset({"không", "khong", "mốt", "tư", "tu", "lăm", "lam", "nhăm"})
# Chữ không dấu trùng từ thường ("sau" = sau đó, "tam" = tạm, "bay", "nam"): chỉ là số khi
# ngay sau là đơn vị tiền / "trăm" / "mươi" (vd "nam trieu", "sau tram").
_WEAK_INITIAL = frozenset({"sau", "tam", "bay", "nam"})
# Hàng đơn vị đứng sau "mươi"/chữ số kiểu nói tắt: "hai mốt" = 21, "hai lăm" = 25.
_COLLOQ_UNITS = frozenset({"mốt", "tư", "tu", "lăm", "lam", "nhăm"})
_TEN = frozenset({"mười", "muoi"})
_TENS_SUFFIX = frozenset({"mươi", "muoi", "chục", "chuc"})
_HUNDRED = frozenset({"trăm", "tram"})
_ODD = frozenset({"lẻ", "le", "linh"})
_HALF = frozenset({"rưỡi", "ruoi"})
_NUMBER_WORDS = frozenset({*_DIGIT_WORDS, *_TEN, *_TENS_SUFFIX, *_HUNDRED, *_ODD, *_HALF})
# Chữ số viết KHÔNG DẤU: chỉ có nghĩa số khi cả văn bản không dấu. Trong văn bản có dấu,
# "sau" = sau đó, "tam" = tạm, "bay" = bây, "nam" = Nam ... không bao giờ là chữ số.
_ASCII_ONLY_WORDS = frozenset(
    {
        "khong",
        "mot",
        "bon",
        "tu",
        "nam",
        "lam",
        "sau",
        "bay",
        "tam",
        "chin",
        "muoi",
        "tram",
        "le",
        "chuc",
        "ruoi",
        "nghin",
        "ngan",
        "canh",
        "xi",
        "lit",
        "cu",
        "ty",
        "dong",
    }
)
_DIACRITIC_RE = re.compile("[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]")

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
# Đơn vị tiền mà nếu đứng sau từ ước lượng ("mấy củ", "vài triệu") → hỏi lại, không đoán.
_VAGUE_WORDS = frozenset({"vài", "vai", "mấy", "may", "dăm", "dam", "chục", "mươi"})

PERIOD_DAYS: dict[str, int] = {
    "ngày": 1,
    "ngay": 1,
    "hôm": 1,
    "hom": 1,
    "bữa": 1,
    "bua": 1,
    "tuần": 7,
    "tuan": 7,
    "tháng": 30,
    "thang": 30,
}
_YEAR_WORDS = frozenset({"năm", "nam"})
_YEAR_DAYS = 365
# Đơn vị "kỳ" (đếm kỳ trả, không phải số ngày). KHÔNG có danh sách lượng từ khác: mọi từ lạ
# ("3 app", "3 giờ", "2 lần") đứng sau số → mơ hồ (whitelist ranh giới, xem _is_boundary).
_INSTALLMENT_WORDS = frozenset({"kỳ", "kì", "ky", "ki"})
# Từ mở đầu cụm thời gian — ranh giới rõ khi ngay sau là số / đơn vị kỳ: "sau 14 ngày".
_TIME_PREPOSITIONS = frozenset({"sau", "trong", "vòng", "vong"})
# Động từ / liên từ mở đầu mệnh đề mới — ranh giới rõ: "vay 1 tỷ 2 trả 1 tỷ rưỡi".
# (Whitelist đóng gồm ĐỘNG TỪ/LIÊN TỪ, không phải danh từ; "lãi", "phí" KHÔNG có ở đây.)
_CLAUSE_VERBS = frozenset(
    {
        "trả", "tra", "góp", "gop", "đóng", "nhận", "nhan", "vay", "mượn", "muon",
        "thì", "thi", "và", "va", "nhưng", "nhung", "còn", "rồi",
    }
)  # fmt: skip
# Đơn vị lóng dễ trùng danh từ thường ("2 chai nước", "2 lít xăng", "3 củ khoai").
_SLANG_UNITS = frozenset({"chai", "lít", "lit", "xị", "xi", "củ", "cu", "cành", "canh"})
# "năm nay", "năm ngoái", "năm 2024" — mốc lịch, không phải số 5 hay thời hạn.
_CALENDAR_AFTER_YEAR = frozenset(
    {"nay", "ngoái", "ngoai", "trước", "truoc", "sau", "tới", "toi", "kia", "rồi", "roi"}
)
_DATE_DAY_WORDS = frozenset({"ngày", "ngay", "mùng", "mồng", "mung", "mong"})
_MONTH_WORDS = frozenset({"tháng", "thang"})
_PER_PERIOD_PREFIX = frozenset({"mỗi", "moi", "hàng", "hang", "mọi", "một", "mot", "1"})
_EVERY = frozenset({"mỗi", "moi", "hàng", "hang", "mọi"})

# Số dính "%" ("3%", "20 %") là một token riêng, không phải số đếm / thời hạn / tiền.
_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*(?:\s*%)?|[^\W\d_]+", re.UNICODE)
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
    """Một cụm tiền. ``start``/``end`` là vị trí ký tự trong văn bản đã NFKC.

    ``value_vnd is None`` ⇔ ``ambiguous``: có cụm tiền nhưng không đọc chắc được số — phải hỏi
    lại người dùng, tuyệt đối không đoán.
    """

    raw: str
    value_vnd: int | None
    start: int
    end: int
    per_period_days: int | None = None
    # False khi mốc kỳ là "một/1 <kỳ>" (có thể là thời hạn: "vay 10 củ một tháng").
    per_period_explicit: bool = True
    ambiguous: bool = False
    # Số kỳ đi sau ("ba kỳ") — số tiền chắc, nhưng cần hỏi lại: tiền mỗi kỳ hay tổng?
    follow_quantity: str | None = None
    # Số chắc nhưng ngữ cảnh lạ ("đóng 3 triệu ngày 15", "2 chai nước") → xác nhận tay.
    uncertain: bool = False


@dataclass(frozen=True, slots=True)
class DurationMention:
    raw: str
    days: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class SlangNormalization:
    source_text: str  # văn bản đã NFKC + lower
    normalized_text: str  # cụm tiền chắc chắn thay bằng "<số> đồng"
    money: tuple[MoneyMention, ...]
    durations: tuple[DurationMention, ...]

    @property
    def needs_confirmation(self) -> bool:
        return any(m.ambiguous or m.uncertain or m.follow_quantity for m in self.money)


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
    """Trả (giá trị, số chữ số khi KHÔNG có dấu phân cách — để đọc '12tr5'; 0 nếu có dấu)."""
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


def _to_int(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


class _Stream:
    """Truy cập token trong CÙNG một mệnh đề."""

    def __init__(
        self,
        tokens: list[_Token],
        clause: int,
        limit: int | None = None,
        *,
        accented: bool = True,
        source: str = "",
    ) -> None:
        self.tokens = tokens
        self.clause = clause
        self.limit = len(tokens) if limit is None else limit
        self.accented = accented
        self.source = source

    def text(self, k: int) -> str | None:
        if 0 <= k < self.limit and self.tokens[k].clause == self.clause:
            return self.tokens[k].text
        return None

    def peek(self, k: int) -> str | None:
        """Token tại ``k`` trong cùng mệnh đề, BỎ QUA giới hạn ``limit`` (chỉ để nhìn trước)."""
        if 0 <= k < len(self.tokens) and self.tokens[k].clause == self.clause:
            return self.tokens[k].text
        return None

    def num(self, k: int) -> str | None:
        """Token tại ``k`` nếu nó MANG NGHĨA SỐ (chữ số / từ số hợp lệ trong chế độ hiện tại)."""
        t = self.text(k)
        if t is None:
            return None
        if _NUMERIC_RE.fullmatch(t):
            return t
        if t not in _NUMBER_WORDS:
            return None
        if self.accented and t in _ASCII_ONLY_WORDS:
            return None
        if not self.accented and t == "sau":
            nxt = self.peek(k + 1)
            if _NUMERIC_RE.fullmatch(nxt or "") or nxt in PERIOD_DAYS:
                return None  # "sau 14 ngay", "sau thang" = sau đó, không phải 6
        return t

    def scale(self, k: int) -> int | None:
        t = self.text(k)
        if t is None or t not in _SCALE_WORDS:
            return None
        if self.accented and t in _ASCII_ONLY_WORDS:
            return None
        if t in {"ngàn", "ngan"} and self.text(k + 1) in {"hàng", "hang"}:
            return None  # "ngân hàng" viết không dấu, không phải nghìn
        return _SCALE_WORDS[t]

    def slash_before(self, k: int) -> bool:
        """Có "/" ngay trước token ``k`` ("500k/ngày")."""
        if k <= 0 or k >= len(self.tokens):
            return False
        return "/" in self.source[self.tokens[k - 1].end : self.tokens[k].start]

    def bounded(self, limit: int) -> _Stream:
        """Dòng token bị chặn trước ``limit`` — để đọc đúng một đoạn con, không đọc lố."""
        return _Stream(
            self.tokens,
            self.clause,
            min(limit, self.limit),
            accented=self.accented,
            source=self.source,
        )


def _is_numeric(tok: str | None) -> bool:
    return tok is not None and _NUMERIC_RE.fullmatch(tok) is not None


def _is_calendar_year(s: _Stream, k: int) -> bool:
    """``năm 2024`` / ``năm nay`` / ``năm ngoái`` tại vị trí ``k``."""
    if s.text(k) not in _YEAR_WORDS:
        return False
    nxt = s.text(k + 1)
    if nxt in _CALENDAR_AFTER_YEAR:
        return True
    return _is_numeric(nxt) and nxt is not None and nxt.isdigit() and int(nxt) >= 1900


def _date_span(s: _Stream, k: int) -> int | None:
    """``ngày 15 tháng 3 [năm 2024]`` / ``tháng 3 năm 2024`` bắt đầu ở ``k`` → vị trí kết thúc."""
    t = s.text(k)
    end: int | None = None
    if t in _DATE_DAY_WORDS and _is_numeric(s.text(k + 1)) and s.text(k + 2) in _MONTH_WORDS:
        end = k + 3
        if _is_numeric(s.text(end)):
            end += 1
    elif t in _MONTH_WORDS and _is_numeric(s.text(k + 1)) and _is_calendar_year(s, k + 2):
        end = k + 2
    if end is None:
        return None
    if _is_calendar_year(s, end):
        end += 2
    return end


@dataclass(frozen=True, slots=True)
class _Group:
    value: Decimal
    end: int
    has_hundred: bool
    numeric_digits: int | None  # số chữ số nếu là 1 token chữ số trần (không dấu phân cách)
    single_word: bool  # đúng 1 chữ số bằng chữ


def _parse_group(s: _Stream, k: int, *, after_scale: bool = False) -> _Group | None:
    """Đọc MỘT số < 1000 (bằng chữ) hoặc một token chữ số, bắt đầu ở ``k``."""
    t = s.num(k)
    if t is None:
        return None
    if _NUMERIC_RE.fullmatch(t):
        value, digits = _parse_numeric(t)
        end = k + 1
        if s.num(end) in _HUNDRED:
            return _Group(value * 100, end + 1, True, None, False)
        if s.num(end) in _TENS_SUFFIX:
            return _Group(value * 10, end + 1, False, None, False)
        return _Group(value, end, False, digits if digits > 0 else None, False)

    def word(j: int) -> str | None:
        return s.num(j)

    def digit(j: int, *, initial: bool) -> int | None:
        w = word(j)
        if w is None or w not in _DIGIT_WORDS:
            return None
        if initial and w in _NON_INITIAL and not after_scale:  # "tám triệu tư", "hai triệu mốt"
            return None
        return _DIGIT_WORDS[w]

    # Không dấu: "tam thoi", "bay gio", "nam nay" — chỉ là số khi ngay sau là đơn vị tiền /
    # "tram" / "muoi" (cả sau đơn vị tiền: "1 trieu tam" → mơ hồ, không đoán 1,8tr).
    # Nhìn trước KHÔNG theo giới hạn đoạn con: "sau | muoi hom" vẫn có thể là "sáu mươi".
    ahead = s.peek(k + 1)
    weak_ok = ahead in _SCALE_WORDS or ahead in _HUNDRED or ahead in _TENS_SUFFIX
    if not s.accented and t in _WEAK_INITIAL and not weak_ok:
        return None

    j = k
    acc: int = 0
    has_hundred = False
    d = digit(j, initial=True)
    if d is not None and word(j + 1) in _HUNDRED:
        acc, j, has_hundred = d * 100, j + 2, True
        if word(j) in _HALF:
            return _Group(Decimal(acc + 50), j + 1, True, None, False)
        if word(j) in _ODD:
            u = digit(j + 1, initial=False)
            if u is None:
                return None
            return _Group(Decimal(acc + u), j + 2, True, None, False)
        d = digit(j, initial=False)
        if d is None and word(j) not in _TEN:
            return _Group(Decimal(acc), j, True, None, False)
        if d is not None and word(j + 1) not in _TENS_SUFFIX:
            # "hai trăm hai" = 220, "một trăm tư" = 140 (nói tắt hàng chục).
            u_w = word(j + 1)
            if u_w in _COLLOQ_UNITS:
                return _Group(
                    Decimal(acc + d * 10 + _DIGIT_WORDS[u_w or ""]), j + 2, True, None, False
                )
            return _Group(Decimal(acc + d * 10), j + 1, True, None, False)

    # Hàng chục + đơn vị
    if word(j) in _TEN and (d is None or has_hundred):
        tens, j = 10, j + 1
        u_tok = word(j)
        if u_tok in _DIGIT_WORDS and u_tok not in _YEAR_WORDS and u_tok not in {"không", "khong"}:
            return _Group(
                Decimal(acc + tens + _DIGIT_WORDS[u_tok or ""]), j + 1, has_hundred, None, False
            )
        return _Group(Decimal(acc + tens), j, has_hundred, None, False)
    if d is None:
        return None
    if word(j + 1) in _TENS_SUFFIX:
        tens, j = d * 10, j + 2
        u_tok = word(j)
        if u_tok in _DIGIT_WORDS and u_tok not in _YEAR_WORDS and u_tok not in {"không", "khong"}:
            return _Group(
                Decimal(acc + tens + _DIGIT_WORDS[u_tok or ""]), j + 1, has_hundred, None, False
            )
        return _Group(Decimal(acc + tens), j, has_hundred, None, False)
    if not has_hundred and word(j + 1) in _COLLOQ_UNITS:
        return _Group(Decimal(d * 10 + _DIGIT_WORDS[word(j + 1) or ""]), j + 2, False, None, False)
    return _Group(Decimal(acc + d), j + 1, has_hundred, None, not has_hundred)


def _is_number_start(s: _Stream, k: int) -> bool:
    t = s.num(k)
    return t is not None and t not in _HALF


def _fraction(s: _Stream, start: int, end: int, scale: int) -> Decimal | None:
    """Giá trị phần lẻ ``tokens[start:end]`` sau đơn vị ``scale``; ``None`` nếu không hợp lệ.

    Chỉ nhận: 1 chữ số bằng chữ ("tư" → 0,4), 1 token chữ số ≤ 3 ký tự ("200" → 0,2;
    "05" → 0,05), hoặc một nhóm có "trăm" ("hai trăm" → 0,2). Không cộng thô "mười", "hai
    mươi" ... vì không phân biệt được phần lẻ với số đếm.
    """
    if scale < 1_000:
        return None
    g = _parse_group(s.bounded(end), start, after_scale=True)
    if g is None or g.end != end:
        return None
    if scale < 100_000 and not g.has_hundred:
        # "50k một" (50.100? 51.000?) — sau nghìn/k chỉ nhận nhóm có "trăm" ("năm nghìn hai
        # trăm" = 5.200, đọc chuẩn hàng đơn vị); chữ số trần / 1 chữ → mơ hồ.
        return None
    if g.numeric_digits is not None:
        # "1 triệu 2" / "1 triệu 200" rõ; "3 triệu 30" (3,3tr hay 3.030.000?) và ≥ 4 chữ số → mơ hồ.
        if g.numeric_digits not in (1, 3):
            return None
        return g.value * scale / (Decimal(10) ** g.numeric_digits)
    if g.single_word:
        return None if g.value == 0 else g.value * scale / 10
    if g.has_hundred and g.value < 1000:
        return g.value * scale / 1000
    return None


def _maybe_fraction(s: _Stream, start: int, end: int, scale: int) -> bool:
    """Đoạn ``tokens[start:end]`` CÓ THỂ là phần lẻ (dù không đọc chắc được giá trị)?

    "tám mươi" sau "triệu" có thể là 80 nghìn; "30" có thể là 300 nghìn hoặc 30 nghìn — đủ để
    tạo một cách đọc cạnh tranh, buộc kết quả thành mơ hồ thay vì đoán.
    """
    if _fraction(s, start, end, scale) is not None:
        return True
    g = _parse_group(s.bounded(end), start, after_scale=True)
    return g is not None and g.end == end and g.value > 0


TailKind = Literal["end", "fraction", "compound", "ambiguous"]


@dataclass(frozen=True, slots=True)
class _Tail:
    kind: TailKind
    end: int  # vị trí kết thúc cụm tiền
    value: Decimal = Decimal(0)
    group: _Group | None = None


# Số đếm hợp lý trước đơn vị kỳ (vay tiêu dùng ≤ 10 năm); vượt → không phải số đếm.
_COUNT_LIMITS: dict[str, int] = {
    **{w: 3650 // d for w, d in PERIOD_DAYS.items()},
    **dict.fromkeys(_INSTALLMENT_WORDS, 360),
}
_YEAR_COUNT_LIMIT = 10
_ONE = frozenset({"một", "mot", "1"})


# Kỳ trần + số / mốc ("ngày 15", "ngày mốt", "tháng sau", "tuần tới") là THAM CHIẾU thời
# điểm, không phải đơn vị đếm hay "mỗi kỳ".
_DATE_REF_AFTER = frozenset(
    {"mốt", "mot", "sau", "tới", "toi", "này", "nay", "kia", "trước", "truoc", "nữa", "nua"}
)


def _is_date_reference(s: _Stream, k: int) -> bool:
    if s.text(k) not in PERIOD_DAYS:
        return False
    nxt = s.peek(k + 1)
    return _is_numeric(nxt) or nxt in _DATE_REF_AFTER


def _is_counting_unit(s: _Stream, k: int) -> bool:
    t = s.text(k)
    if t is None or _date_span(s, k) is not None or _is_date_reference(s, k):
        return False
    if t in PERIOD_DAYS or t in _INSTALLMENT_WORDS:
        return True
    return (
        t in _YEAR_WORDS
        and not _is_calendar_year(s, k)
        and not (s.accented and t in _ASCII_ONLY_WORDS)
    )


def _is_boundary(s: _Stream, k: int) -> bool:
    """Ranh giới RÕ sau một số (whitelist). Chỉ khi đó số sau đơn vị tiền mới là phần lẻ.

    Gồm: hết mệnh đề / dấu câu, đ/đồng/vnd, "mỗi/hàng + kỳ", "/kỳ", mốc lịch ("năm 2024"),
    ngày tháng ("ngày 15 tháng 3"), "sau/trong + số|kỳ" ("sau 14 ngày"). Mọi từ khác → không.
    """
    t = s.text(k)
    if t is None:
        return True
    if t in _CURRENCY_TERMINAL and not (s.accented and t in _ASCII_ONLY_WORDS):
        return True
    if t in _EVERY and s.text(k + 1) in PERIOD_DAYS:
        return True
    if t in PERIOD_DAYS and s.slash_before(k):
        return True
    if _is_calendar_year(s, k) or _date_span(s, k) is not None:
        return True
    if t in _CLAUSE_VERBS and not (s.accented and t in _ASCII_ONLY_WORDS):
        return True
    if t in _TIME_PREPOSITIONS:
        prev = s.peek(k - 1)
        if not s.accented and t == "sau" and prev in _NUMBER_WORDS:
            return False  # "...sau muoi sau 1 tuan": "sau" có thể là chữ số 6 nối tiếp
        nxt = s.peek(k + 1)
        if _is_numeric(nxt) or nxt in PERIOD_DAYS:
            return True
        # Không dấu: "sau mot thang" — "sau" có thể là 6 → chỉ nhận khi văn bản có dấu.
        return s.accented and s.num(k + 1) is not None
    return False


def _run_end(s: _Stream, k: int) -> int:
    """Cuối dãy token số liên tiếp từ ``k`` (dừng trước mốc lịch / "năm" chỉ thời hạn)."""
    j = k
    while _is_number_start(s, j):
        t = s.text(j)
        if _is_calendar_year(s, j):
            break
        nxt = s.text(j + 1)
        if j > k and t in _YEAR_WORDS and nxt not in PERIOD_DAYS and nxt not in _INSTALLMENT_WORDS:
            prev = s.text(j - 1)
            if _is_numeric(prev) or prev in _DIGIT_WORDS or prev in _TEN or prev in _TENS_SUFFIX:
                break  # "một năm", "2 năm", "mười năm"
        j += 1
    return j


def _resolve_tail(s: _Stream, k: int, scale: int, glued: bool) -> _Tail:
    """Phần sau một đơn vị tiền: phần lẻ / số đếm của kỳ / hợp số — hoặc mơ hồ."""
    t0 = s.text(k)
    if not s.accented and t0 == "sau" and _is_boundary(s, k):
        # Không dấu ngay sau đơn vị tiền: "mot trieu sau 1 tuan" = 1,6tr hay "sau 1 tuần"?
        return _Tail("ambiguous", k + 1)
    if t0 in _YEAR_WORDS and s.peek(k + 1) in {"sau", "tới", "toi", "trước", "truoc"}:
        # "5 triệu năm sau (14 ngày)": "năm sau" (năm tới) hay 5,5tr + "sau ..."? → hỏi lại.
        # ("năm nay", "năm ngoái", "năm 2024" vẫn là mốc lịch rõ.)
        return _Tail("ambiguous", k + 1)
    if not _is_number_start(s, k) or _is_calendar_year(s, k):
        return _Tail("end", k)
    k_end = _run_end(s, k)
    if k_end == k:
        return _Tail("end", k)
    nxt_scale = s.scale(k_end)
    if nxt_scale is not None and nxt_scale < scale:
        g = _parse_group(s.bounded(k_end), k, after_scale=True)
        if g is not None and g.end == k_end:
            return _Tail("compound", k_end, group=g)
        return _Tail("ambiguous", k_end)
    if glued and k_end == k + 1 and _is_numeric(s.text(k)) and not _is_counting_unit(s, k_end):
        # "7tr5", "1tr200" viết liền: chắc chắn là phần lẻ, bất kể từ theo sau.
        frac = _fraction(s, k, k_end, scale)
        if frac is not None:
            return _Tail("fraction", k_end, value=frac)
    if _is_boundary(s, k_end):
        frac = _fraction(s, k, k_end, scale)
        if frac is not None:
            return _Tail("fraction", k_end, value=frac)
        return _Tail("ambiguous", k_end)
    if not _is_counting_unit(s, k_end):
        return _Tail("ambiguous", k_end)  # "2 triệu 3 app", "3 giờ", "2 lãi" → hỏi lại
    x = s.text(k_end) or ""
    limit = _COUNT_LIMITS.get(x, _YEAR_COUNT_LIMIT)
    candidates: list[_Tail] = []
    if not glued:
        whole = _parse_group(s.bounded(k_end), k)
        if whole is not None and whole.end == k_end and 0 < whole.value <= limit:
            candidates.append(_Tail("end", k))
    one_split = k_end - 1
    if one_split > k and s.text(one_split) in _ONE:
        frac = _fraction(s, k, one_split, scale)
        if frac is not None:
            candidates.append(_Tail("fraction", one_split, value=frac))  # "1 triệu 200 một tháng"
    for split in range(k + 1, k_end):
        if split == one_split and candidates and candidates[-1].kind == "fraction":
            continue
        count = _parse_group(s.bounded(k_end), split)
        if count is None or count.end != k_end or not 0 < count.value <= limit:
            continue
        if _maybe_fraction(s, k, split, scale):
            candidates.append(_Tail("ambiguous", k_end))  # cách đọc cạnh tranh
    if len(candidates) == 1 and candidates[0].kind != "ambiguous":
        return candidates[0]
    return _Tail("ambiguous", k_end)


@dataclass(frozen=True, slots=True)
class _Amount:
    value: Decimal | None  # None ⇔ mơ hồ
    end: int
    is_money: bool
    slang_unit: bool = False  # có dùng chai/lít/củ/xị/cành


def _parse_amount(s: _Stream, k: int) -> _Amount | None:
    g = _parse_group(s, k)
    if g is None:
        return None
    total = Decimal(0)
    last_scale: int | None = None
    pos = g.end
    group: _Group = g
    slang = False
    while True:
        unit = s.text(pos)
        unit_scale = s.scale(pos)
        if unit_scale is not None and (last_scale is None or unit_scale < last_scale):
            scale = unit_scale
            slang = slang or unit in _SLANG_UNITS
            total += group.value * scale
            last_scale = scale
            pos += 1
            if s.text(pos) in _HALF:
                total += Decimal(scale) / 2
                pos += 1
                break
            glued = s.tokens[pos - 1].end == s.tokens[pos].start if s.text(pos) else False
            tail = _resolve_tail(s, pos, scale, glued)
            if tail.kind == "compound" and tail.group is not None:
                group, pos = tail.group, tail.end
                continue
            if tail.kind == "ambiguous":
                return _Amount(None, tail.end, True, slang)
            total += tail.value
            pos = tail.end
            break
        if last_scale is None:
            if unit in _CURRENCY_TERMINAL and not (s.accented and unit in _ASCII_ONLY_WORDS):
                return _Amount(group.value, pos + 1, True)
            return _Amount(group.value, pos, False)
        break
    if s.text(pos) in _CURRENCY_TERMINAL:
        pos += 1
    return _Amount(total, pos, True, slang)


def _period_after(s: _Stream, k: int) -> tuple[int | None, int, bool, bool]:
    """Đơn vị kỳ ngay sau cụm tiền → (số ngày/kỳ, vị trí kết thúc, rõ ràng?, cần xác nhận?).

    Chắc chắn: ``.../ngày``, ``mỗi/hàng tuần``, ``một tháng``, hoặc kỳ trần ở CUỐI mệnh đề
    ("250k ngày"). Kỳ trần còn chữ theo sau ("đóng 3 triệu ngày 15", "trả 6 triệu tháng
    sau", "ngày mốt") → KHÔNG coi là mỗi kỳ, đánh dấu cần xác nhận.
    """
    t = s.text(k)
    nxt = s.text(k + 1)
    if t in PERIOD_DAYS:
        if _date_span(s, k) is not None:
            return None, k, True, True
        if s.slash_before(k) or nxt is None:
            return PERIOD_DAYS[t], k + 1, True, False
        return None, k, True, True
    if t in _PER_PERIOD_PREFIX and nxt in PERIOD_DAYS and _date_span(s, k + 1) is None:
        return PERIOD_DAYS[nxt], k + 2, t not in _ONE, False
    return None, k, True, False


def _period_before(s: _Stream, i: int) -> int | None:
    """``mỗi tuần đóng chín xị rưỡi``: mốc ``mỗi/hàng + kỳ`` đứng trước trong cùng mệnh đề."""
    for k in range(i - 1, max(0, i - 5) - 1, -1):
        t = s.text(k)
        if t is None:
            break
        if t in PERIOD_DAYS and k >= 1 and s.text(k - 1) in _EVERY:
            return PERIOD_DAYS[t]
    return None


def _quantity_after(s: _Stream, k: int) -> str | None:
    g = _parse_group(s, k)
    if g is not None and s.text(g.end) in _INSTALLMENT_WORDS:
        return " ".join(s.tokens[j].text for j in range(k, g.end + 1))
    return None


_PERCENT_WORDS = (("phần", "trăm"), ("phan", "tram"))


def _after_percent(s: _Stream, i: int) -> bool:
    """Số ngay sau "3%" / "3 phần trăm" là kỳ của LÃI SUẤT ("lãi 3% một tháng"), không phải
    thời hạn vay."""
    prev = s.text(i - 1)
    if prev is not None and prev.endswith("%"):
        return True
    return (s.text(i - 2), prev) in _PERCENT_WORDS


def normalize_vn_currency_slang(text: str) -> SlangNormalization:
    """Tìm và chuẩn hóa mọi cụm tiền / thời hạn trong một câu tiếng Việt bình dân."""
    source = normalize_text(text)
    tokens = _tokenize(source)
    accented = _DIACRITIC_RE.search(source) is not None
    money: list[MoneyMention] = []
    durations: list[DurationMention] = []
    i = 0
    while i < len(tokens):
        s = _Stream(tokens, tokens[i].clause, accented=accented, source=source)
        date_end = _date_span(s, i)
        if date_end is not None:
            i = date_end
            continue
        if s.scale(i) is not None and i > 0 and s.text(i - 1) in _VAGUE_WORDS:
            # "mấy củ", "vài triệu": có tiền nhưng không biết bao nhiêu → hỏi lại.
            start, end = tokens[i - 1].start, tokens[i].end
            money.append(MoneyMention(source[start:end], None, start, end, ambiguous=True))
            i += 1
            continue
        parsed = _parse_amount(s, i)
        if parsed is None:
            i += 1
            continue
        start = tokens[i].start
        k = parsed.end
        if parsed.is_money:
            if parsed.value is None:
                end = tokens[k - 1].end
                money.append(MoneyMention(source[start:end], None, start, end, ambiguous=True))
                i = k
                continue
            per_period, k2, explicit, unsure = _period_after(s, k)
            if per_period is None:
                per_period = _period_before(s, i)
                k2 = k
            end = tokens[k2 - 1].end
            follow = s.text(k2)
            # "2 chai nước", "2 lít xăng": đơn vị lóng kèm danh từ thường → xác nhận tay.
            unsure = unsure or (
                parsed.slang_unit
                and follow is not None
                and not _is_numeric(follow)
                and not follow.endswith("%")
                and follow not in _HALF
            )
            money.append(
                MoneyMention(
                    raw=source[start:end],
                    value_vnd=_to_int(parsed.value),
                    start=start,
                    end=end,
                    per_period_days=per_period,
                    per_period_explicit=explicit,
                    follow_quantity=_quantity_after(s, k2),
                    uncertain=unsure,
                )
            )
            i = k2
            continue
        # Không có đơn vị tiền → có thể là thời hạn: "ba tuần", "30 ngày", "một năm".
        unit = s.text(k)
        days_per: int | None = PERIOD_DAYS.get(unit) if unit is not None else None
        if days_per is None and _is_counting_unit(s, k) and unit in _YEAR_WORDS:
            days_per = _YEAR_DAYS
        if (
            days_per is not None
            and parsed.value is not None
            and _date_span(s, k) is None
            and not _after_percent(s, i)
        ):
            # "15 tháng 3": số + tháng + số (không phải tiền) là ngày tháng, không phải thời hạn.
            is_date = (
                unit in _MONTH_WORDS
                and _is_numeric(s.text(k + 1))
                and s.text(k + 2) not in _SCALE_WORDS
            )
            if not is_date:
                days = parsed.value * days_per
                end_k = k + 1
                if s.text(end_k) in _HALF:
                    days += Decimal(days_per) / 2
                    end_k += 1
                end = tokens[end_k - 1].end
                durations.append(DurationMention(source[start:end], _to_int(days), start, end))
                i = end_k
                continue
        i = max(parsed.end, i + 1)
    normalized = source
    for m in sorted(money, key=lambda m: m.start, reverse=True):
        if m.value_vnd is not None:
            normalized = normalized[: m.start] + f"{m.value_vnd} đồng" + normalized[m.end :]
    return SlangNormalization(source, normalized, tuple(money), tuple(durations))


def parse_vnd_amount(text: str) -> int | None:
    """Đọc MỘT cụm tiền (``"10 tr"``, ``"10.000k"``, ``"10,000,000đ"``) → int đồng.

    ``None`` nếu không có cụm tiền mang đơn vị, hoặc cụm tiền mơ hồ (phải hỏi lại); số trần
    không đơn vị cũng nhận nếu toàn bộ chuỗi chỉ là một số (``"10000000"``).
    """
    result = normalize_vn_currency_slang(text)
    if result.money:
        return result.money[0].value_vnd
    stripped = normalize_text(text).strip()
    if _NUMERIC_RE.fullmatch(stripped):
        return _to_int(_parse_numeric(stripped)[0])
    return None
