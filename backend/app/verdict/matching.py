"""Đối chiếu bên cho vay tuyên bố với ``LenderRegistry`` (File 2 §1.3 chặng 4, S2-EC16).

Quy tắc (review vòng 3):

- Bằng chứng chính: tên thương hiệu, domain, package Android, và tên pháp nhân CHỈ KHI nó trùng
  đúng ``official_legal_name`` (không rỗng) của registry. Khớp khác trên tên pháp nhân (lõi, nhái)
  chỉ là tín hiệu phụ — registry hiện chưa có tên pháp nhân để đối chiếu.
- Bằng chứng chính trỏ tới >= 2 pháp nhân → ``conflict`` (tín hiệu độc lập cho mạo danh).
- Một trường EXACT thì trường khác nhái pháp nhân khác KHÔNG ghi đè EXACT; nó thành tín hiệu.
- LOOKALIKE chỉ là tín hiệu: ma trận chỉ ra ĐỎ mạo danh khi có thêm tín hiệu độc lập.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.verdict.inputs import Provenance, VerdictInput
from app.verdict.lookalike import (
    BrandMatch,
    MatchStatus,
    detect_lookalike_brand,
    detect_lookalike_domain,
)
from app.verdict.registry import LenderRecord, LenderRegistry

__all__ = [
    "SIGNAL_DOMAIN_LOOKALIKE",
    "SIGNAL_LEGAL_NAME_LOOKALIKE",
    "SIGNAL_NAME_LOOKALIKE",
    "LenderMatch",
    "match_lender",
]

SIGNAL_NAME_LOOKALIKE = "NAME_LOOKALIKE"
SIGNAL_DOMAIN_LOOKALIKE = "DOMAIN_LOOKALIKE"
SIGNAL_LEGAL_NAME_LOOKALIKE = "LEGAL_NAME_LOOKALIKE"


@dataclass(frozen=True, slots=True)
class LenderMatch:
    """Kết quả đối chiếu pháp nhân.

    - ``EXACT``: trùng tên chuẩn/alias/domain/package của ``record``.
    - ``LOOKALIKE``: nhái ``record`` (``source`` = trường gây ra: NAME | DOMAIN | LEGAL), hoặc
      ``conflict`` (bằng chứng chính trỏ tới 2 pháp nhân khác nhau).
    - ``NONE``: không thuộc registry (app/cá nhân không phép).

    ``signals``: các tín hiệu nhái phụ (NAME/DOMAIN/LEGAL_NAME_LOOKALIKE) đi kèm.
    """

    status: MatchStatus
    record: LenderRecord | None = None
    score: float = 0.0
    method: str | None = None
    conflict: bool = False
    source: str | None = None
    signals: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def none(cls) -> LenderMatch:
        return cls(MatchStatus.NONE)

    @classmethod
    def exact(cls, record: LenderRecord) -> LenderMatch:
        return cls(MatchStatus.EXACT, record, 1.0, "EXACT_NAME", source="NAME")


def _package_match(provenance: Provenance, registry: LenderRegistry) -> BrandMatch | None:
    pkg = provenance.package_name
    if not pkg:
        return None
    for record in registry.active_records():
        if pkg in record.android_packages:
            return BrandMatch(MatchStatus.EXACT, record, 1.0, "EXACT_PACKAGE")
    return None


def _strong_legal(hit: BrandMatch) -> bool:
    record = hit.record
    return (
        hit.status is MatchStatus.EXACT
        and hit.method == "EXACT_NAME"
        and record is not None
        and bool(record.official_legal_name.strip())
        and hit.matched_name == record.official_legal_name
    )


def _code(hit: BrandMatch) -> str:
    return hit.record.entity_code if hit.record is not None else ""


def match_lender(
    extraction: VerdictInput, provenance: Provenance, registry: LenderRegistry
) -> LenderMatch:
    name = detect_lookalike_brand(extraction.claimed_lender_name, registry)
    legal = detect_lookalike_brand(extraction.claimed_company_legal_name, registry)
    domain = detect_lookalike_domain(extraction.claimed_domain, registry)
    pkg = _package_match(provenance, registry)

    primary: list[tuple[str, BrandMatch]] = [
        (src, h)
        for src, h in (("NAME", name), ("DOMAIN", domain), ("PACKAGE", pkg))
        if h is not None and h.status is MatchStatus.EXACT and h.record is not None
    ]
    if _strong_legal(legal):
        primary.append(("LEGAL", legal))

    signals: set[str] = set()
    if name.status is MatchStatus.LOOKALIKE:
        signals.add(SIGNAL_NAME_LOOKALIKE)
    if domain.status is MatchStatus.LOOKALIKE:
        signals.add(SIGNAL_DOMAIN_LOOKALIKE)
    if legal.status is MatchStatus.LOOKALIKE or (
        legal.status is MatchStatus.EXACT
        and not _strong_legal(legal)
        and primary
        and _code(legal) not in {_code(h) for _, h in primary}
    ):
        signals.add(SIGNAL_LEGAL_NAME_LOOKALIKE)
    frozen = frozenset(signals)

    codes = {_code(h) for _, h in primary}
    if len(codes) > 1:
        src, first = primary[0]
        return LenderMatch(
            MatchStatus.LOOKALIKE, first.record, 1.0, "ENTITY_CONFLICT", True, src, frozen
        )
    if primary:
        src, first = primary[0]
        return LenderMatch(MatchStatus.EXACT, first.record, 1.0, first.method, False, src, frozen)

    looks = [
        (src, h)
        for src, h in (("NAME", name), ("DOMAIN", domain), ("LEGAL", legal))
        if h.status is MatchStatus.LOOKALIKE and h.record is not None
    ]
    if looks:
        src, best = looks[0]  # ưu tiên trường tên thương hiệu
        if provenance.apk_signature_ok is True and pkg is not None and _code(pkg) == _code(best):
            return LenderMatch(
                MatchStatus.EXACT, best.record, 1.0, "EXACT_PACKAGE", False, "PACKAGE"
            )
        return LenderMatch(
            MatchStatus.LOOKALIKE, best.record, best.score, best.method, False, src, frozen
        )
    if legal.status is MatchStatus.EXACT and legal.record is not None:
        return LenderMatch(MatchStatus.EXACT, legal.record, 1.0, legal.method, False, "LEGAL")
    return LenderMatch.none()
