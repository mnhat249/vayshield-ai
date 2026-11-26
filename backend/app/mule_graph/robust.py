"""Gom cụm kháng đầu độc vector (File 3 §5.2, EC-AI20).

- ``canonicalize_intent``: tóm tắt cốt lõi ≤ 200 ký tự TRƯỚC khi nhúng — câu rác/chèn dài vô nghĩa
  không chứa mệnh đề thao túng bị loại, nên vector không bị làm loãng.
- ``huber_spherical_medoid``: tâm trung vị trên mặt cầu đơn vị với mất mát Huber theo khoảng cách
  góc; là một PHẦN TỬ của tập đầu vào (medoid) nên vài vector ngoại lai không kéo lệch tâm.

Thuần, không I/O; chỉ dùng thư viện chuẩn.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Final

MAX_INTENT_CHARS: Final = 200
DEFAULT_HUBER_DELTA: Final = 0.6  # radian (~34 độ)

# Giả định (không có trong đặc tả): từ khóa mệnh đề thao túng cốt lõi, không dấu, chữ thường.
_MANIPULATION_KEYWORDS: Final = (
    "chuyen khoan",
    "phi ",
    "dat coc",
    "giai ngan",
    "otp",
    "bao hiem",
    "xac minh",
    "ho so",
    "khoan vay",
    "cai app",
    "cai dat",
    "link",
    "tai khoan",
    "stk",
    "bao lanh",
    "thu tuc",
    "nop tien",
    "dong phi",
    "huy khoan",
    "cong an",
)
_SENTENCE_SPLIT_RE: Final = re.compile(r"(?<=[.!?;])\s+|\n+")
_WS_RE: Final = re.compile(r"\s+")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _score(sentence: str) -> int:
    folded = _fold(sentence) + " "
    return sum(1 for k in _MANIPULATION_KEYWORDS if k in folded)


def canonicalize_intent(text: str, *, max_chars: int = MAX_INTENT_CHARS) -> str:
    """Tóm tắt ≤ ``max_chars`` ký tự: giữ câu có từ khóa thao túng (theo thứ tự gốc), bỏ câu lặp."""
    if max_chars < 1:
        raise ValueError("max_chars phải >= 1")
    normalized = unicodedata.normalize("NFC", text)
    sentences: list[str] = []
    seen: set[str] = set()
    for raw in _SENTENCE_SPLIT_RE.split(normalized):
        sentence = _WS_RE.sub(" ", raw).strip()
        key = _fold(sentence)
        if not sentence or key in seen:
            continue
        seen.add(key)
        sentences.append(sentence)
    scored = [(_score(s), i, s) for i, s in enumerate(sentences)]
    kept = [t for t in scored if t[0] > 0] or scored[:1]
    kept.sort(key=lambda t: (-t[0], t[1]))  # câu giàu mệnh đề nhất trước, hòa → theo thứ tự gốc
    chosen: list[tuple[int, str]] = []
    used = 0
    for _, idx, sentence in kept:
        extra = len(sentence) + (1 if chosen else 0)
        if used + extra > max_chars:
            continue
        chosen.append((idx, sentence))
        used += extra
    if not chosen:
        return kept[0][2][:max_chars].rstrip() if kept else ""
    chosen.sort()
    return " ".join(s for _, s in chosen)


def _unit(vec: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0 or not math.isfinite(norm):
        raise ValueError("vector không chuẩn hóa được (0 hoặc không hữu hạn)")
    return tuple(x / norm for x in vec)


def _huber(distance: float, delta: float) -> float:
    if distance <= delta:
        return 0.5 * distance * distance
    return delta * (distance - 0.5 * delta)


def huber_spherical_medoid(
    vectors: Sequence[Sequence[float]], *, delta: float = DEFAULT_HUBER_DELTA
) -> int:
    """Chỉ số của spherical medoid: phần tử cực tiểu tổng mất mát Huber của khoảng cách góc."""
    if not vectors:
        raise ValueError("cần ít nhất 1 vector")
    if len({len(v) for v in vectors}) != 1:
        raise ValueError("các vector phải cùng số chiều")
    units = [_unit(v) for v in vectors]
    best_idx = 0
    best_cost = math.inf
    for i, a in enumerate(units):
        cost = 0.0
        for j, b in enumerate(units):
            if i == j:
                continue
            cos = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b, strict=True))))
            cost += _huber(math.acos(cos), delta)
        if cost < best_cost - 1e-12:
            best_cost, best_idx = cost, i
    return best_idx


def robust_centroid(
    vectors: Sequence[Sequence[float]], *, delta: float = DEFAULT_HUBER_DELTA
) -> tuple[float, ...]:
    """Centroid kháng ngoại lai = spherical medoid (đơn vị hóa)."""
    return _unit(vectors[huber_spherical_medoid(vectors, delta=delta)])
