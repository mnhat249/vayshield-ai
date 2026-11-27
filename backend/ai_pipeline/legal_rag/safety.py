"""Hậu kiểm đầu ra LLM (EC-AI14 + zero legal hallucination).

Đầu ra chỉ hợp lệ khi (sau NFC + bỏ dấu): không có từ cấm / lời khuyên né nợ; MỌI token số
(kể cả %, năm, số điện thoại) và mọi tham chiếu luật (điều/khoản/bộ luật/luật/nghị định/
thông tư/nghị quyết) đều xuất hiện như token liên tiếp trong các node được phép (hoặc câu hỏi
gốc). Không đạt -> caller quay về văn bản tất định.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from .models import LegalNode
from .text import fold

BANNED_TERMS: tuple[str, ...] = (
    "amortization",
    "usury",
    "promissory",
    "plaintiff",
    "pursuant",
    "hereby",
    "liability",
    "article",
    "penal",
    "decree",
    "statute",
)

# Cụm (đã bỏ dấu) cấm xuất hiện trong lời giải thích.
_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "bung no",
    "bung app",
    "tron no",
    "ne no",
    "xu no",
    "quit no",
    "xoa no",
    "khong can tra",
    "khong phai tra no",
    "khong tra no",
    "khong can hoan tra",
)

_ARTICLE_MARKERS = {"dieu", "khoan", "diem"}


def _toks(text: str) -> list[str]:
    return fold(unicodedata.normalize("NFC", text)).split()


def _contains_seq(hay: Sequence[str], needle: Sequence[str]) -> bool:
    n = len(needle)
    return n > 0 and any(list(hay[i : i + n]) == list(needle) for i in range(len(hay) - n + 1))


def _is_doc_marker(tokens: list[str], i: int) -> bool:
    nxt = tokens[i + 1 : i + 2]
    return (tokens[i] == "nghi" and nxt in (["dinh"], ["quyet"])) or (
        tokens[i] == "thong" and nxt == ["tu"]
    )


def _forbidden(tokens: list[str]) -> bool:
    joined = " ".join(tokens)
    if any(term in joined for term in BANNED_TERMS):
        return True
    padded = f" {joined} "
    return any(f" {p} " in padded for p in _FORBIDDEN_PHRASES)


def allowed_corpus(nodes: Sequence[LegalNode], extra_text: str = "") -> list[str]:
    parts = [n.searchable_text() + " " + (n.doc_number if n.verified else "") for n in nodes]
    return _toks(" ".join([*parts, extra_text]))


def rewrite_is_safe(text: str, nodes: Sequence[LegalNode], extra_text: str = "") -> bool:
    tokens = _toks(text)
    if not tokens or _forbidden(tokens):
        return False
    corpus = allowed_corpus(nodes, extra_text)
    corpus_set = set(corpus)
    # 1) mọi token số phải có trong tập được phép
    for t in tokens:
        if any(ch.isdigit() for ch in t) and t not in corpus_set:
            return False
    # 2) tham chiếu luật phải khớp theo cụm token liên tiếp
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in _ARTICLE_MARKERS:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if not nxt or not _contains_seq(corpus, [t, nxt]):
                return False
        elif t == "bo" and tokens[i + 1 : i + 2] == ["luat"]:
            phrase = tokens[i : i + 4]
            if len(phrase) < 4 or not _contains_seq(corpus, phrase):
                return False
        elif t == "luat" and (i == 0 or tokens[i - 1] != "bo"):
            phrase = tokens[i : i + 2]
            if len(phrase) < 2 or not _contains_seq(corpus, phrase):
                return False
        elif _is_doc_marker(tokens, i):
            phrase = tokens[i : i + 3]
            if len(phrase) < 3 or not _contains_seq(corpus, phrase):
                return False
        i += 1
    return True
