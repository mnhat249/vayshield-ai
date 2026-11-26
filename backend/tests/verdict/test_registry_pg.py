"""Repo PG của ``LenderRegistry``: ánh xạ dòng (không cần PG) + đọc thật (mark ``pg``)."""

from __future__ import annotations

import json
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.schemas.common import LegalCategory
from app.verdict import (
    LenderRegistry,
    MatchStatus,
    Provenance,
    VerdictInput,
    detect_lookalike_brand,
    match_lender,
)
from app.verdict.registry_pg import PgLenderRegistry, RegistryNotLoadedError, record_from_row


def test_record_from_row_maps_jsonb_packages_and_arrays() -> None:
    row: dict[str, object] = {
        "entity_code": "FINCO_X",
        "legal_category": "LICENSED_FINCO",
        "official_short_name": "X Finance",
        "official_legal_name": "Công ty Tài chính X",
        "official_domains": ["x.vn"],
        "official_hotlines": ["1900 0009", None],
        "android_packages": json.dumps([{"pkg": "vn.x.app", "sha256": "ab" * 32}, "vn.x.lite"]),
        "sandbox_cap_vnd": None,
    }
    rec = record_from_row(row)
    assert rec.legal_category is LegalCategory.LICENSED_FINCO
    assert rec.android_packages == ("vn.x.app", "vn.x.lite")
    assert rec.official_hotlines == ("1900 0009",)
    assert rec.aliases == ()  # chưa có cột aliases → cần migration mới
    assert rec.registry_id is None


def test_record_from_row_p2p_cap() -> None:
    row: dict[str, object] = {
        "entity_code": "P2P_X",
        "legal_category": "P2P_SANDBOX",
        "official_short_name": "P",
        "official_legal_name": "P JSC",
        "official_domains": None,
        "official_hotlines": None,
        "android_packages": [],
        "sandbox_cap_vnd": 100_000_000,
    }
    assert record_from_row(row).p2p_cap_vnd == 100_000_000


def test_record_from_row_maps_registry_id() -> None:
    rid = uuid.uuid4()
    row: dict[str, object] = {
        "id": str(rid),
        "entity_code": "B",
        "legal_category": "COMMERCIAL_BANK",
        "official_short_name": "B",
        "official_legal_name": "B",
    }
    assert record_from_row(row).registry_id == rid
    row["id"] = rid
    assert record_from_row(row).registry_id == rid


def test_unrefreshed_pg_registry_fails_closed() -> None:
    """Re-review: registry PG chưa nạp KHÔNG được trả rỗng (sẽ tắt im lặng cờ mạo danh)."""
    reg = PgLenderRegistry(engine=None)  # type: ignore[arg-type]
    assert isinstance(reg, LenderRegistry)
    assert not reg.loaded
    with pytest.raises(RegistryNotLoadedError):
        reg.active_records()
    with pytest.raises(RegistryNotLoadedError):
        reg.get("FINCO_HOMECREDIT")
    inp = VerdictInput(claimed_lender_name="Home Credit")
    with pytest.raises(RegistryNotLoadedError):
        match_lender(inp, Provenance(beneficiary_is_personal_account=True), reg)


@pytest.mark.pg
async def test_pg_registry_reads_active_rows(pg: AsyncEngine, pg_admin: AsyncEngine) -> None:
    code = f"T09_{uuid.uuid4().hex[:12]}".upper()
    gone = f"{code}_OLD"
    async with pg_admin.begin() as c:
        for entity, active in ((code, True), (gone, False)):
            await c.execute(
                text(
                    "INSERT INTO licensed_lender_registry (entity_code, legal_category,"
                    " official_short_name, official_legal_name, normalized_name, official_domains,"
                    " official_hotlines, android_packages, is_active) VALUES (:c, 'LICENSED_FINCO',"
                    " :s, :l, :n, ARRAY['t09demo.vn'], ARRAY['1900 0000'], CAST(:p AS JSONB), :a)"
                ),
                {
                    "c": entity,
                    "s": f"Zq{entity}",
                    "l": f"Cong ty Tai chinh Zq{entity}",
                    "n": entity.lower(),
                    "p": json.dumps([{"pkg": "vn.t09.demo"}]),
                    "a": active,
                },
            )
    try:
        reg = PgLenderRegistry(pg)
        assert await reg.refresh(date.today()) >= 1
        assert reg.loaded
        rec = reg.get(code)
        assert rec is not None and rec.android_packages == ("vn.t09.demo",)
        assert rec.registry_id is not None
        assert rec.official_domains == ("t09demo.vn",)
        assert reg.get(gone) is None
        assert detect_lookalike_brand(rec.official_short_name, reg).status is MatchStatus.EXACT
    finally:
        async with pg_admin.begin() as c:
            await c.execute(
                text("DELETE FROM licensed_lender_registry WHERE entity_code IN (:a, :b)"),
                {"a": code, "b": gone},
            )
