"""Mô hình dữ liệu Legal GraphRAG (File 3 §5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Final

W_COSINE: Final = 0.65
W_BM25: Final = 0.35

UNVERIFIED_NOTE: Final = "Nguồn chưa được đối chiếu với văn bản chính thức."


@dataclass(frozen=True)
class LegalNode:
    """Một Điều/Khoản nguyên tử (bảng ``legal_provision_nodes``)."""

    clause_code: str
    doc_type: str
    doc_number: str
    effective_date: date
    is_currently_valid: bool
    title_vi: str
    verbatim_content: str
    plain_summary_vi: str
    applicable_verdicts: tuple[str, ...]
    verified: bool = False

    def is_in_force(self, today: date) -> bool:
        """EC-AI16: cùng điều kiện với ``is_currently_valid AND effective_date <= today``."""
        return self.is_currently_valid and self.effective_date <= today

    @property
    def has_verbatim(self) -> bool:
        return bool(self.verbatim_content.strip())

    def searchable_text(self) -> str:
        parts = (self.title_vi, self.verbatim_content, self.plain_summary_vi)
        return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class LegalEdge:
    source_clause: str
    target_clause: str
    relation_type: str


@dataclass(frozen=True)
class Citation:
    """Trích dẫn chỉ gồm node CÓ nguyên văn. Không bao giờ ngoài ``applicable_verdicts``."""

    clause_code: str
    title_vi: str
    quote: str
    verified: bool
    # Chỉ có khi verified=True (không hiển thị số hiệu/ngày chưa đối chiếu).
    doc_number: str | None = None
    effective_date: date | None = None
    note: str | None = None


@dataclass(frozen=True)
class PendingSource:
    """Node gắn với phán quyết nhưng chưa có nguyên văn: chỉ nêu tên, không trích."""

    clause_code: str
    title_vi: str
    note: str = "Chưa có nguyên văn đã xác minh để trích dẫn. " + UNVERIFIED_NOTE


@dataclass(frozen=True)
class Explanation:
    verdict_code: str
    plain_vi: str
    citations: tuple[Citation, ...]
    pending_sources: tuple[PendingSource, ...] = ()
    fully_verified: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "plain_vi": self.plain_vi,
            "citations": [
                {
                    "clause_code": c.clause_code,
                    "title_vi": c.title_vi,
                    "quote": c.quote,
                    "verified": c.verified,
                    "doc_number": c.doc_number,
                    "effective_date": c.effective_date.isoformat() if c.effective_date else None,
                    "note": c.note,
                }
                for c in self.citations
            ],
        }


@dataclass(frozen=True)
class SearchHit:
    clause_code: str
    title_vi: str
    score: float
    cosine: float
    bm25: float
    via_expansion: bool = False
    verified: bool = False
    doc_number: str | None = None
    effective_date: date | None = None
    snippet: str = ""
    note: str | None = None


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    refused: bool = False
    refusal_message: str | None = None
    hits: tuple[SearchHit, ...] = field(default_factory=tuple)
