"""Lớp 5: chuẩn hóa NFKC + chống lách từ khóa / ký tự đồng hình (EC-AI23, S2-EC19)."""

from __future__ import annotations

import re
import unicodedata

_INVISIBLE = dict.fromkeys(
    [*range(0x200B, 0x2010), 0xFEFF, 0x2060, 0x00AD, *range(0x202A, 0x202F)], None
)

# Cyrillic/Greek giả dạng Latin → Latin.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i",
        "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d", "ӏ": "l", "к": "k", "м": "m", "т": "t",
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
        "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S",
        "ο": "o", "α": "a", "ν": "v", "ρ": "p", "ι": "i", "κ": "k", "τ": "t",
        "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
        "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    }
)  # fmt: skip

_LETTER = r"[^\W\d_]"
_SEP = r"[.\-_*·•]"
# Chuỗi chữ cái đơn tách bằng . - _ * ·  (vd "p.h.í") → nối lại. Chỉ chữ cái: "1.5%" an toàn.
_SPLIT_RUN = re.compile(rf"(?<!\w)({_LETTER}(?:{_SEP}{_LETTER})+)(?!\w)")
_SEP_RE = re.compile(_SEP)
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"}
_LEET_RE = re.compile(rf"(?<={_LETTER})([013457])(?={_LETTER})")
_WS = re.compile(r"[ \t ]+")


def canonicalize_text(text: str) -> str:
    """NFKC → bỏ ký tự zero-width → ánh xạ homoglyph → nối chữ bị chèn dấu → giải leet.

    Leet chỉ áp dụng khi chữ số nằm giữa hai chữ cái ("kh0ản" → "khoản"), nên số
    tiền/lãi suất như "1.5%", "100k", "3tr" không bị đổi.
    """
    out = unicodedata.normalize("NFKC", text).translate(_INVISIBLE)
    out = out.translate(_HOMOGLYPHS)
    out = _SPLIT_RUN.sub(lambda m: _SEP_RE.sub("", m.group(1)), out)
    out = _LEET_RE.sub(lambda m: _LEET[m.group(1)], out)
    out = unicodedata.normalize("NFKC", out)
    return _WS.sub(" ", out).strip()


def fold_diacritics(text: str) -> str:
    """Bỏ dấu tiếng Việt + lowercase, dùng cho so khớp từ khóa tất định."""
    nfd = unicodedata.normalize("NFD", canonicalize_text(text).lower())
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return stripped.replace("đ", "d")
