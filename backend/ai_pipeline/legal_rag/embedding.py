"""Adapter embedding. Vector trong DB seed là GIẢ (hash) nên luôn tính lại qua adapter."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

from .text import tokenize

DIM = 768


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LexicalHashEmbedder:
    """Embedder ngoại tuyến tất định: băm từ không dấu vào 768 chiều (feature hashing).

    Chỉ là phương án dự phòng khi chưa có Gemini; KHÔNG phải embedding ngữ nghĩa.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        v = [0.0] * DIM
        for tok in tokenize(text):
            h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:4], "big")
            v[h % DIM] += 1.0 if (h >> 31) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        return [x / norm for x in v] if norm else v
