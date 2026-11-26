"""Dữ liệu dùng chung cho test ma trận phán quyết.

Registry là GIẢ (tên/hotline/domain minh họa), không phải seed thật của T08.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.schemas.common import LegalCategory
from app.verdict import AprInterval, LenderRecord, VerdictObject

HOME_CREDIT = LenderRecord(
    entity_code="FINCO_HOMECREDIT",
    legal_category=LegalCategory.LICENSED_FINCO,
    official_short_name="Home Credit",
    official_legal_name="Công ty Tài chính TNHH MTV Home Credit Việt Nam",
    official_domains=("homecredit.vn",),
    official_hotlines=("1900 0001",),
    android_packages=("vn.homecredit.demo",),
)
FE_CREDIT = LenderRecord(
    entity_code="FINCO_FECREDIT",
    legal_category=LegalCategory.LICENSED_FINCO,
    official_short_name="FE Credit",
    official_legal_name="Công ty Tài chính TNHH Ngân hàng Việt Nam Thịnh Vượng SMBC",
    official_domains=("fecredit.com.vn",),
    official_hotlines=("1900 0002",),
)
SHINHAN = LenderRecord(
    entity_code="FINCO_SHINHAN",
    legal_category=LegalCategory.LICENSED_FINCO,
    official_short_name="Shinhan Finance",
    official_legal_name="Công ty Tài chính TNHH MTV Shinhan Việt Nam",
    official_hotlines=("1900 0003",),
)
VIETCOMBANK = LenderRecord(
    entity_code="BANK_VCB",
    legal_category=LegalCategory.COMMERCIAL_BANK,
    official_short_name="Vietcombank",
    official_legal_name="Ngân hàng TMCP Ngoại thương Việt Nam",
    official_domains=("vietcombank.com.vn",),
)
VIETINBANK = LenderRecord(
    entity_code="BANK_VIETIN",
    legal_category=LegalCategory.COMMERCIAL_BANK,
    official_short_name="VietinBank",
    official_legal_name="Ngân hàng TMCP Công Thương Việt Nam",
)
P2P_DEMO = LenderRecord(
    entity_code="P2P_DEMO",
    legal_category=LegalCategory.P2P_SANDBOX,
    official_short_name="Lendzy Demo",
    official_legal_name="Công ty Cổ phần Lendzy Demo",
    sandbox_cap_vnd=100_000_000,
)
PAWN_DEMO = LenderRecord(
    entity_code="PAWN_DEMO",
    legal_category=LegalCategory.LICENSED_PAWN_CHAIN,
    official_short_name="Cầm Đồ Kimlong Demo",
    official_legal_name="Công ty Cổ phần Kimlong Demo",
)
BNPL_DEMO = LenderRecord(
    entity_code="BNPL_DEMO",
    legal_category=LegalCategory.BNPL_WALLET_PROVIDER,
    official_short_name="Paylater Zeta",
    official_legal_name="Công ty Cổ phần Zeta Pay",
)
RETIRED = LenderRecord(
    entity_code="FINCO_RETIRED",
    legal_category=LegalCategory.LICENSED_FINCO,
    official_short_name="Oldcorp Finance",
    official_legal_name="Công ty Tài chính Oldcorp",
    is_active=False,
)

ALL_RECORDS = (
    HOME_CREDIT,
    FE_CREDIT,
    SHINHAN,
    VIETCOMBANK,
    VIETINBANK,
    P2P_DEMO,
    PAWN_DEMO,
    BNPL_DEMO,
    RETIRED,
)

Decide = Callable[..., VerdictObject]


def apr(lo: float, hi: float | None = None) -> AprInterval:
    """Khoảng APR (phân số/năm); ``hi=None`` → điểm."""
    return AprInterval.from_bounds(lo, lo if hi is None else hi)
