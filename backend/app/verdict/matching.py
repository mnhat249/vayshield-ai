"""Đối chiếu bên cho vay tuyên bố với ``LenderRegistry`` (File 2 §1.3 chặng 4, S2-EC16)."""

from __future__ import annotations

from dataclasses import dataclass

from app.verdict.inputs import Provenance, VerdictInput
from app.verdict.lookalike import (
    BrandMatch,
    MatchStatus,
    detect_lookalike_brand,
    detect_lookalike_domain,
)
from app.verdict.registry import LenderRecord, LenderRegistry

__all__ = ["LenderMatch", "match_lender"]


@dataclass(frozen=True, slots=True)
class LenderMatch:
    """Kết quả đối chiếu pháp nhân.

    - ``EXACT``: trùng tên chuẩn/alias/domain/package của ``record``.
    - ``LOOKALIKE``: tên/domain nhái ``record`` (hoặc ``conflict``: các trường trỏ tới 2 pháp nhân
      khác nhau) → cờ mạo danh.
    - ``NONE``: không thuộc registry (app/cá nhân không phép).
    """

    status: MatchStatus
    record: LenderRecord | None = None
    score: float = 0.0
    method: str | None = None
    conflict: bool = False

    @classmethod
    def none(cls) -> LenderMatch:
        return cls(MatchStatus.NONE)

    @classmethod
    def exact(cls, record: LenderRecord) -> LenderMatch:
        return cls(MatchStatus.EXACT, record, 1.0, "EXACT_NAME")


def _package_match(provenance: Provenance, registry: LenderRegistry) -> BrandMatch | None:
    pkg = provenance.package_name
    if not pkg:
        return None
    for record in registry.active_records():
        if pkg in record.android_packages:
            return BrandMatch(MatchStatus.EXACT, record, 1.0, "EXACT_PACKAGE")
    return None


def match_lender(
    extraction: VerdictInput, provenance: Provenance, registry: LenderRegistry
) -> LenderMatch:
    hits: list[BrandMatch] = [
        detect_lookalike_brand(extraction.claimed_lender_name, registry),
        detect_lookalike_brand(extraction.claimed_company_legal_name, registry),
        detect_lookalike_domain(extraction.claimed_domain, registry),
    ]
    pkg = _package_match(provenance, registry)
    if pkg is not None:
        hits.append(pkg)
    exact = [h for h in hits if h.status is MatchStatus.EXACT and h.record is not None]
    looks = [h for h in hits if h.status is MatchStatus.LOOKALIKE and h.record is not None]
    exact_codes = {h.record.entity_code for h in exact if h.record is not None}
    if len(exact_codes) > 1:
        first = exact[0]
        return LenderMatch(MatchStatus.LOOKALIKE, first.record, 1.0, "ENTITY_CONFLICT", True)
    if looks:
        best = max(looks, key=lambda h: h.score)
        same_entity = bool(exact_codes) and all(
            h.record is not None and h.record.entity_code in exact_codes for h in looks
        )
        # Chỉ chữ ký APK đúng (tất định, T03) mới cho phép bỏ qua sai lệch tên do OCR.
        if same_entity and provenance.apk_signature_ok is True:
            return LenderMatch(MatchStatus.EXACT, exact[0].record, 1.0, exact[0].method)
        return LenderMatch(MatchStatus.LOOKALIKE, best.record, best.score, best.method)
    if exact:
        return LenderMatch(MatchStatus.EXACT, exact[0].record, 1.0, exact[0].method)
    return LenderMatch.none()
