"""S2-EC16: Fuzzy Lookalike & Homoglyph (Levenshtein + Jaro-Winkler > 0.78) + LenderRegistry."""

from __future__ import annotations

import pytest

from app.verdict import (
    LOOKALIKE_THRESHOLD,
    InMemoryLenderRegistry,
    LenderRegistry,
    MatchStatus,
    brand_similarity,
    detect_lookalike_brand,
    detect_lookalike_domain,
)
from app.verdict.lookalike import normalize_host, normalize_text
from tests.verdict._helpers import FE_CREDIT, HOME_CREDIT, RETIRED, SHINHAN, VIETCOMBANK


def test_threshold_is_spec_value() -> None:
    assert LOOKALIKE_THRESHOLD == 0.78


def test_normalize_strips_vietnamese_diacritics() -> None:
    assert normalize_text("CÔNG TY TÀI CHÍNH Đông Á") == "cong ty tai chinh dong a"


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Home Credit", "FINCO_HOMECREDIT"),
        ("HOME CREDIT", "FINCO_HOMECREDIT"),
        ("HomeCredit", "FINCO_HOMECREDIT"),
        ("CÔNG TY TÀI CHÍNH TNHH MTV HOME CREDIT VIỆT NAM", "FINCO_HOMECREDIT"),
        ("Vietcombank", "BANK_VCB"),
        ("VietinBank", "BANK_VIETIN"),
        ("FE CREDIT", "FINCO_FECREDIT"),
    ],
)
def test_ec16_exact_official_names_are_not_lookalike(
    registry: InMemoryLenderRegistry, name: str, code: str
) -> None:
    """S2-EC16: tên chuẩn / tên pháp nhân chuẩn → EXACT, không bị đánh cờ giả mạo."""
    hit = detect_lookalike_brand(name, registry)
    assert hit.status is MatchStatus.EXACT
    assert hit.record is not None and hit.record.entity_code == code


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("Tài chính HomeCredit Quốc Tế", HOME_CREDIT),
        ("Tín dụng FE Việt", FE_CREDIT),
        ("Shinhan Vay Nhanh", SHINHAN),
        ("H0me Credit", HOME_CREDIT),  # homoglyph số 0
        ("Homе Credit", HOME_CREDIT),  # 'е' Cyrillic
        ("Home Credlt Vay", HOME_CREDIT),
        ("Vietconbank", VIETCOMBANK),
    ],
)
def test_ec16_lookalike_names_flagged(
    registry: InMemoryLenderRegistry, name: str, target: object
) -> None:
    """S2-EC16: ví dụ trong đặc tả + homoglyph đều bị gắn LOOKALIKE đúng thương hiệu bị nhái."""
    hit = detect_lookalike_brand(name, registry)
    assert hit.status is MatchStatus.LOOKALIKE
    assert hit.record is target


@pytest.mark.parametrize(
    "name", ["Vay Tiền Nhanh 24h", "Cầm đồ Hưng Thịnh", "Tiệm vàng Kim Phát", "", "   ", None]
)
def test_ec16_unrelated_names_no_match(registry: InMemoryLenderRegistry, name: str | None) -> None:
    assert detect_lookalike_brand(name, registry).status is MatchStatus.NONE


def test_ec16_threshold_is_strict_greater_than_boundary(registry: InMemoryLenderRegistry) -> None:
    """S2-EC16 biên: score == ngưỡng → KHÔNG nhái; nhỉnh hơn → nhái (điều kiện là ``> 0.78``)."""
    probe = "Homi Kredit"
    score = brand_similarity(probe, "Home Credit")
    assert 0.5 < score < 1.0
    at = detect_lookalike_brand(probe, registry, threshold=score)
    assert at.status is not MatchStatus.LOOKALIKE or at.method == "TOKEN_CONTAINMENT"
    just_below = detect_lookalike_brand(probe, registry, threshold=score - 1e-9)
    assert just_below.status is MatchStatus.LOOKALIKE
    assert just_below.record is HOME_CREDIT


def test_ec16_score_just_below_threshold_not_flagged(registry: InMemoryLenderRegistry) -> None:
    """S2-EC16 biên dưới: tên chỉ hơi giống (score <= 0.78) không bị gắn cờ."""
    probe = "Hanoi Crest"
    assert brand_similarity(probe, "Home Credit") <= LOOKALIKE_THRESHOLD
    assert detect_lookalike_brand(probe, registry).status is MatchStatus.NONE


def test_inactive_record_ignored(registry: InMemoryLenderRegistry) -> None:
    assert detect_lookalike_brand(RETIRED.official_short_name, registry).status is MatchStatus.NONE
    assert registry.get(RETIRED.entity_code) is None
    assert registry.get(HOME_CREDIT.entity_code) is HOME_CREDIT


def test_registry_rejects_duplicate_codes() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        InMemoryLenderRegistry([HOME_CREDIT, HOME_CREDIT])


def test_in_memory_registry_satisfies_protocol(registry: InMemoryLenderRegistry) -> None:
    assert isinstance(registry, LenderRegistry)


@pytest.mark.parametrize(
    "domain",
    ["homecredit.vn", "https://www.homecredit.vn/vay", "app.homecredit.vn", "HOMECREDIT.VN."],
)
def test_ec16_official_domain_exact(registry: InMemoryLenderRegistry, domain: str) -> None:
    hit = detect_lookalike_domain(domain, registry)
    assert hit.status is MatchStatus.EXACT
    assert hit.record is HOME_CREDIT


@pytest.mark.parametrize(
    "domain",
    ["homecredit-vn.com", "homecredlt.vn", "h0mecredit.vn", "homecredit.vn.vay-nhanh.xyz"],
)
def test_ec16_lookalike_domain_flagged(registry: InMemoryLenderRegistry, domain: str) -> None:
    """S2-EC16: lệch domain chính thức (thêm hậu tố, homoglyph, subdomain giả) → LOOKALIKE."""
    hit = detect_lookalike_domain(domain, registry)
    assert hit.status is MatchStatus.LOOKALIKE
    assert hit.record is HOME_CREDIT


def test_unrelated_domain_no_match(registry: InMemoryLenderRegistry) -> None:
    assert detect_lookalike_domain("tiemvangkimphat.vn", registry).status is MatchStatus.NONE
    assert detect_lookalike_domain(None, registry).status is MatchStatus.NONE


def test_normalize_host() -> None:
    assert normalize_host("HTTPS://WWW.FeCredit.com.vn:443/a?b=1") == "fecredit.com.vn"


@pytest.mark.parametrize(
    ("name", "target"),
    [
        ("Home Credit Việt Nam", HOME_CREDIT),
        ("Công ty Tài chính Home Credit", HOME_CREDIT),
        ("HOME CREDIT VN", HOME_CREDIT),
        ("Shinhan Finance Việt Nam", SHINHAN),
        ("Vietcombank VN", VIETCOMBANK),
        ("Ngân hàng Vietcombank", VIETCOMBANK),
        ("Công ty Tài chính FE Credit", FE_CREDIT),
    ],
)
def test_ec16_real_name_variants_with_benign_words_are_exact_core(
    registry: InMemoryLenderRegistry, name: str, target: object
) -> None:
    """Review BLOCKER (test âm S2-EC16): tên thật + "Việt Nam"/"VN"/"Công ty Tài chính" KHÔNG
    phải tên nhái."""
    hit = detect_lookalike_brand(name, registry)
    assert hit.status is MatchStatus.EXACT, (name, hit)
    assert hit.method == "EXACT_CORE"
    assert hit.record is target


@pytest.mark.parametrize(
    "name", ["Home Credit Vay Nhanh", "Vietcombank Online 24h", "FE Credit Quốc Tế"]
)
def test_ec16_suspicious_suffix_blocks_exact_core(
    registry: InMemoryLenderRegistry, name: str
) -> None:
    assert detect_lookalike_brand(name, registry).status is MatchStatus.LOOKALIKE


@pytest.mark.parametrize("name", ["Credit Nhanh", "MCredit", "Home Pay", "Vietcom"])
def test_ec16_weak_or_short_cores_not_assigned_to_brand(
    registry: InMemoryLenderRegistry, name: str
) -> None:
    """Review minor: lõi ngắn/token phổ biến không bị gán là nhái FE Credit / Home Credit / VCB."""
    assert detect_lookalike_brand(name, registry).status is MatchStatus.NONE


@pytest.mark.parametrize("domain", ["credit.vn", "vietcom.vn", "mcredit.vn"])
def test_ec16_short_domain_labels_not_assigned_to_brand(
    registry: InMemoryLenderRegistry, domain: str
) -> None:
    assert detect_lookalike_domain(domain, registry).status is MatchStatus.NONE


def test_ec16_ambiguous_core_shared_by_two_entities_is_not_exact() -> None:
    from app.core.schemas.common import LegalCategory
    from app.verdict import LenderRecord

    bank = LenderRecord(
        entity_code="BANK_SHINHAN",
        legal_category=LegalCategory.COMMERCIAL_BANK,
        official_short_name="Shinhan Bank",
        official_legal_name="Ngân hàng TNHH MTV Shinhan Việt Nam",
    )
    reg = InMemoryLenderRegistry([SHINHAN, bank])
    assert detect_lookalike_brand("Shinhan Việt Nam", reg).method != "EXACT_CORE"
