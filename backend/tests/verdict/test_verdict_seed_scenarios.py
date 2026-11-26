"""Kịch bản phán quyết (review vòng 3, attack2) trên registry dựng từ ``seeds/data.py`` thật."""

from __future__ import annotations

import pytest

from app.core.schemas.common import LegalCategory
from app.core.verdict_codes import VerdictCode
from app.verdict import (
    AprInterval,
    Contradiction,
    InMemoryLenderRegistry,
    LenderMatch,
    LenderRecord,
    MatchStatus,
    Provenance,
    VerdictInput,
    VerdictObject,
    decide_verdict,
    match_lender,
)
from seeds.data import LENDERS

V = VerdictCode
REG = InMemoryLenderRegistry(
    LenderRecord(
        entity_code=lender.entity_code,
        legal_category=LegalCategory(lender.legal_category),
        official_short_name=lender.short_name,
        official_legal_name="",
        aliases=lender.aliases,
        official_hotlines=lender.hotlines,
    )
    for lender in LENDERS
)
IV = AprInterval.from_bounds(0.30, 0.32)
APK_OK = Provenance(apk_signature_ok=True)


def _run(
    name: str | None, legal: str | None, prov: Provenance, iv: AprInterval = IV
) -> tuple[LenderMatch, VerdictObject]:
    inp = VerdictInput(claimed_lender_name=name, claimed_company_legal_name=legal)
    m = match_lender(inp, prov, REG)
    return m, decide_verdict(inp, iv, m, prov)


@pytest.mark.parametrize(
    ("name", "legal", "code"),
    [
        ("JACCS", "Công ty Tài chính TNHH MTV Quốc tế Việt Nam JACCS", "FINCO_JACCS"),
        ("Mcredit", "Công ty Tài chính TNHH MB Shinsei", "FINCO_MCREDIT"),
        (
            "SHB Finance",
            "Công ty Tài chính TNHH MTV Ngân hàng TMCP Sài Gòn Hà Nội",
            "FINCO_SHB_FINANCE",
        ),
        ("MB Bank", "Ngân hàng TMCP Quân đội", "BANK_MBBANK"),
        ("MB", "Ngân hàng TMCP Quân đội", "BANK_MBBANK"),
        ("Home Credit", "Công ty Tài chính TNHH MTV Home Credit Việt Nam", "FINCO_HOME_CREDIT"),
        ("Vietcredit", "Công ty Tài chính Cổ phần Tín Việt", "FINCO_VIETCREDIT"),
    ],
)
@pytest.mark.parametrize("prov", [Provenance(), APK_OK])
def test_real_brand_with_real_legal_name_never_red(
    name: str, legal: str, code: str, prov: Provenance
) -> None:
    """Review vòng 3 mục 4: tên thật + tên pháp nhân thật (registry chưa có legal name) không ĐỎ;
    "Mcredit" + "MB Shinsei" không bị coi là xung đột với MB Bank (token ngắn "MB")."""
    m, v = _run(name, legal, prov)
    assert m.status is MatchStatus.EXACT
    assert m.record is not None and m.record.entity_code == code
    assert not m.conflict
    assert v.verdict_code not in (V.V_RED_BRAND_SPOOF, V.V_RED_CRIMINAL_USURY)
    if prov.apk_signature_ok:
        assert v.verdict_code is V.V_GREEN_TRANSPARENT


@pytest.mark.parametrize("name", ["Viet Credit Pro", "MBBank 24h", "HD Saison Pro"])
def test_lookalike_alone_is_signal_not_red(name: str) -> None:
    m, v = _run(name, None, Provenance(), AprInterval.from_bounds(0.1, 0.1))
    assert m.status is MatchStatus.LOOKALIKE
    assert v.verdict_code is V.V_AMBER_INCOMPLETE_DOC
    assert Contradiction.LOOKALIKE_BRAND in v.contradictions


def test_g02_home_credit_official_personal_account_is_spoof() -> None:
    _, v = _run(
        "Home Credit Official",
        None,
        Provenance(beneficiary_is_personal_account=True),
        AprInterval.from_bounds(0.5, 0.5),
    )
    assert v.verdict_code is V.V_RED_BRAND_SPOOF
    assert v.official_hotline_verify == "1900 633 633"


def test_hd_bank_not_assigned_to_hd_saison() -> None:
    m, _ = _run("HD Bank", None, Provenance())
    assert m.status is MatchStatus.NONE
