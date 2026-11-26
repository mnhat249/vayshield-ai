"""Ma trận 9 phán quyết tất định + phát hiện thương hiệu nhái + chỉ số sinh kế (T09).

Luồng dùng (T11)::

    match = match_lender(inp, provenance, registry)
    interval = AprInterval.from_reports([best_report, worst_report])  # T02 analyze_cashflows
    verdict = decide_verdict(inp, interval, match, provenance)
"""

from app.verdict.fees import DeductedFeeBounds, deducted_fee_bounds, has_bundled_insurance
from app.verdict.inputs import (
    DeductedFee,
    Provenance,
    RolloverTerms,
    SpecialMorphology,
    UpfrontFeeDemand,
    VerdictInput,
    VerdictInputError,
)
from app.verdict.interval import AprInterval, effective_apr
from app.verdict.legal_basis import ALL_LEGAL_BASIS_IDS, LEGAL_BASIS_BY_VERDICT
from app.verdict.lookalike import (
    LOOKALIKE_THRESHOLD,
    BrandMatch,
    MatchStatus,
    brand_similarity,
    detect_lookalike_brand,
    detect_lookalike_domain,
)
from app.verdict.matching import LenderMatch, match_lender
from app.verdict.matrix import RULE_PRIORITY, LenderPath, VerdictObject, decide_verdict
from app.verdict.messages import Contradiction
from app.verdict.registry import (
    P2P_SANDBOX_DEFAULT_CAP_VND,
    InMemoryLenderRegistry,
    LenderRecord,
    LenderRegistry,
)

__all__ = [
    "ALL_LEGAL_BASIS_IDS",
    "LEGAL_BASIS_BY_VERDICT",
    "LOOKALIKE_THRESHOLD",
    "P2P_SANDBOX_DEFAULT_CAP_VND",
    "RULE_PRIORITY",
    "AprInterval",
    "BrandMatch",
    "Contradiction",
    "DeductedFee",
    "DeductedFeeBounds",
    "InMemoryLenderRegistry",
    "LenderMatch",
    "LenderPath",
    "LenderRecord",
    "LenderRegistry",
    "MatchStatus",
    "Provenance",
    "RolloverTerms",
    "SpecialMorphology",
    "UpfrontFeeDemand",
    "VerdictInput",
    "VerdictInputError",
    "VerdictObject",
    "brand_similarity",
    "decide_verdict",
    "deducted_fee_bounds",
    "detect_lookalike_brand",
    "detect_lookalike_domain",
    "effective_apr",
    "has_bundled_insurance",
    "match_lender",
]
