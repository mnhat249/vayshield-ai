"""S2-EC16 — bộ hồi quy cố định ~350 tên trên registry dựng từ ``seeds/data.py`` thật.

Chạy 2 chế độ: có alias (registry PG sau migration 0002) và không alias. ``official_legal_name``
rỗng như seed hiện tại.
"""

from __future__ import annotations

import pytest

from app.core.schemas.common import LegalCategory
from app.verdict import InMemoryLenderRegistry, LenderRecord, MatchStatus, detect_lookalike_brand
from seeds.data import LENDERS
from tests.verdict import lookalike_cases as cases


def _registry(with_aliases: bool) -> InMemoryLenderRegistry:
    return InMemoryLenderRegistry(
        LenderRecord(
            entity_code=lender.entity_code,
            legal_category=LegalCategory(lender.legal_category),
            official_short_name=lender.short_name,
            official_legal_name="",
            aliases=lender.aliases if with_aliases else (),
            official_hotlines=lender.hotlines,
        )
        for lender in LENDERS
    )


REGISTRIES = {"aliases": _registry(True), "no_aliases": _registry(False)}
MODES = pytest.mark.parametrize("mode", list(REGISTRIES))


def _flat(groups: dict[str, list[str]]) -> list[tuple[str, str]]:
    return [(brand, name) for brand, names in groups.items() for name in names]


def test_regression_suite_size() -> None:
    assert len(LENDERS) == 22  # 10 ngân hàng + 12 FinCo
    assert cases.ALL_NAMES_COUNT >= 300


@MODES
@pytest.mark.parametrize(("brand", "name"), _flat(cases.A))
def test_ec16_group_a_real_names_never_lookalike(mode: str, brand: str, name: str) -> None:
    hit = detect_lookalike_brand(name, REGISTRIES[mode])
    assert hit.status is not MatchStatus.LOOKALIKE, (name, hit)
    if hit.status is MatchStatus.EXACT:
        assert hit.record is not None and hit.record.official_short_name == brand


@MODES
@pytest.mark.parametrize(("brand", "name"), _flat(cases.A_MUST_EXACT))
def test_ec16_group_a_must_be_exact(mode: str, brand: str, name: str) -> None:
    hit = detect_lookalike_brand(name, REGISTRIES[mode])
    assert hit.status is MatchStatus.EXACT, (name, hit)
    assert hit.record is not None and hit.record.official_short_name == brand


@MODES
@pytest.mark.parametrize(("brand", "name"), _flat(cases.B))
def test_ec16_group_b_lookalikes_flagged(mode: str, brand: str, name: str) -> None:
    hit = detect_lookalike_brand(name, REGISTRIES[mode])
    assert hit.status is MatchStatus.LOOKALIKE, (name, hit)
    assert hit.record is not None and hit.record.official_short_name == brand


@MODES
@pytest.mark.parametrize("name", cases.C)
def test_ec16_group_c_unrelated_none(mode: str, name: str) -> None:
    hit = detect_lookalike_brand(name, REGISTRIES[mode])
    assert hit.status is MatchStatus.NONE, (name, hit)


@MODES
@pytest.mark.parametrize("name", cases.D_NOT_LOOKALIKE)
def test_ec16_group_d_other_entities_not_lookalike(mode: str, name: str) -> None:
    hit = detect_lookalike_brand(name, REGISTRIES[mode])
    assert hit.status is not MatchStatus.LOOKALIKE, (name, hit)


@MODES
@pytest.mark.parametrize("name", cases.D_NOT_FINCO_EXACT)
def test_ec16_bank_or_sister_company_never_exact_finco(mode: str, name: str) -> None:
    hit = detect_lookalike_brand(name, REGISTRIES[mode])
    assert hit.status is not MatchStatus.LOOKALIKE, (name, hit)
    if hit.status is MatchStatus.EXACT:
        assert hit.record is not None
        assert hit.record.legal_category is not LegalCategory.LICENSED_FINCO, (name, hit)


@MODES
@pytest.mark.parametrize(("name", "brand"), list(cases.KNOWN_SIGNAL_ONLY.items()))
def test_ec16_known_signal_only_cases(mode: str, name: str, brand: str) -> None:
    hit = detect_lookalike_brand(name, REGISTRIES[mode])
    assert hit.status is MatchStatus.LOOKALIKE
    assert hit.record is not None and hit.record.official_short_name == brand
