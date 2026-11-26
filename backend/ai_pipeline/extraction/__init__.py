"""Trạm 2 — trích xuất LLM, Conformal escalation, Voice-to-Cashflow (T04 / WP1-05).

Client Gemini thật: ``ai_pipeline.extraction.genai_client.GoogleGenAIClient`` (import riêng để
test không phụ thuộc mạng).
"""

from .cascade import CascadeConfig, CascadeResult, ExtractionUnavailableError, ModelCascade
from .conformal import ConformalRouter, EscalationReason, RoutingDecision
from .currency_slang import normalize_vn_currency_slang, parse_vnd_amount
from .disentangler import FeeInterval, LoanCard, MultiLoanDisentangler, PageExtraction
from .gemini_client import ApiKeyPool, FakeGeminiClient, GeminiClient, ImagePart
from .pipeline import ExtractionOutcome, ExtractionPipeline
from .schemas import SCHEMA_VERSION, MultiImageLoanExtraction
from .voice import transcribe_terms_to_extraction

__all__ = [
    "SCHEMA_VERSION",
    "ApiKeyPool",
    "CascadeConfig",
    "CascadeResult",
    "ConformalRouter",
    "EscalationReason",
    "ExtractionOutcome",
    "ExtractionPipeline",
    "ExtractionUnavailableError",
    "FakeGeminiClient",
    "FeeInterval",
    "GeminiClient",
    "ImagePart",
    "LoanCard",
    "ModelCascade",
    "MultiImageLoanExtraction",
    "MultiLoanDisentangler",
    "PageExtraction",
    "RoutingDecision",
    "normalize_vn_currency_slang",
    "parse_vnd_amount",
    "transcribe_terms_to_extraction",
]
