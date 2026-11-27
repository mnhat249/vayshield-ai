"""Mô hình dữ liệu Legal GraphRAG (File 3 §5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Final

W_COSINE: Final = 0.65
W_BM25: Final = 0.35

UNVERIFIED_NOTE: Final = "Nguồn chưa được đối chiếu với văn bản chính thức."
# Ngày giữ chỗ của seed (T08) cho "chưa biết ngày hiệu lực": KHÔNG phải ngày hiệu lực thật.
UNKNOWN_EFFECTIVE_DATE: Final = date(1970, 1, 1)
UNKNOWN_DATE_NOTE: Final = "Chưa rõ ngày hiệu lực."


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
    def date_unknown(self) -> bool:
        """Ngày sentinel (kể cả node verified): không được dùng để khẳng định còn hiệu lực."""
        return self.effective_date == UNKNOWN_EFFECTIVE_DATE

    @property
    def shown_effective_date(self) -> date | None:
        return self.effective_date if self.verified and not self.date_unknown else None

    def status_note(self) -> str | None:
        """Ghi chú trạng thái nguồn (None nếu đã xác minh và có ngày thật)."""
        parts: list[str] = []
        if not self.verified:
            parts.append(UNVERIFIED_NOTE)
        if self.date_unknown:
            parts.append(UNKNOWN_DATE_NOTE)
        return " ".join(parts) or None

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
            "fully_verified": self.fully_verified,
            "pending_sources": [
                {"clause_code": p.clause_code, "title_vi": p.title_vi, "note": p.note}
                for p in self.pending_sources
            ],
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
