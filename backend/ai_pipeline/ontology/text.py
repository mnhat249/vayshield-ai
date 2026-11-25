"""Chuẩn hóa tiếng Việt cho bộ phân loại: bỏ dấu, gộp khoảng trắng, sửa nhầm âm STT (EC-AI12)."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^a-z0-9% ]+")

# Phonetic Confusion Tolerant Matching (EC-AI12): STT ồn nghe nhầm âm gần giống.
# Chỉ sửa khi có ngữ cảnh đi kèm ("... khoản vay") để không biến đổi câu vô hại.
_PHONETIC_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(gui|ky|quy|hui)\s+(lai\s+)?(khoan|khoang)\s+vay\b"), "huy khoan vay"),
    (re.compile(r"\b(gui|ky|quy|hui)\s+(hop dong|khoan tien vay)\b"), r"huy \2"),
    (re.compile(r"\bdoi chung\b"), "doi ung"),
    (re.compile(r"\bgiai thoa\b"), "giai toa"),
)


def strip_diacritics(text: str) -> str:
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize(text: str) -> str:
    """lower + bỏ dấu + bỏ ký tự lạ + sửa nhầm âm STT."""
    t = strip_diacritics(text.lower())
    t = _NON_WORD.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    for pattern, repl in _PHONETIC_FIXES:
        t = pattern.sub(repl, t)
    return t
