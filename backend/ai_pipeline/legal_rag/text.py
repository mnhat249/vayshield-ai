"""Chuẩn hóa tiếng Việt không dấu, BM25 và cosine thuần Python (hàm thuần, không I/O)."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence

_NON_WORD = re.compile(r"[^a-z0-9]+")


def fold(text: str) -> str:
    t = text.lower().replace("đ", "d")
    t = unicodedata.normalize("NFD", t)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return _NON_WORD.sub(" ", t).strip()


def tokenize(text: str) -> list[str]:
    return [t for t in fold(text).split() if t]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, dot / (na * nb))


def bm25_scores(
    query_tokens: Sequence[str], docs: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """BM25 Okapi; điểm chuẩn hóa về [0,1] bằng cách chia cho điểm lớn nhất (0 nếu không khớp)."""
    n = len(docs)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs) / n or 1.0
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    raw: list[float] = []
    for d in docs:
        tf = Counter(d)
        s = 0.0
        for term in set(query_tokens):
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = math.log(1.0 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avgdl))
        raw.append(s)
    top = max(raw)
    return [s / top if top > 0 else 0.0 for s in raw]
