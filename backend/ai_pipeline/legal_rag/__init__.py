"""T13: Legal GraphRAG gắn với phán quyết (File 3 §5.1)."""

from .embedding import Embedder, LexicalHashEmbedder
from .guardrail import is_in_scope
from .models import Citation, Explanation, LegalEdge, LegalNode, SearchHit, SearchOutcome
from .repository import InMemoryLegalRepository, LegalRepository, SqlLegalRepository
from .service import LegalRagService, PlainRewriter

__all__ = [
    "Citation",
    "Embedder",
    "Explanation",
    "InMemoryLegalRepository",
    "LegalEdge",
    "LegalNode",
    "LegalRagService",
    "LegalRepository",
    "LexicalHashEmbedder",
    "PlainRewriter",
    "SearchHit",
    "SearchOutcome",
    "SqlLegalRepository",
    "is_in_scope",
]
