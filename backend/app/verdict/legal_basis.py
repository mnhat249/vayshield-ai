"""Mã node căn cứ pháp lý mà mỗi phán quyết được phép viện dẫn (File 3 §5.1 "Verdict-Pinned").

Tầng phán quyết CHỈ trả mã node (``clause_code`` của bảng ``legal_provision_nodes``); không bao
giờ chứa nguyên văn điều luật. T13 (GraphRAG) dùng mã này để nạp đúng node và giải thích.
Mã phải trùng ``clause_code`` do T08 seed (danh sách theo File 1 §1.2 + mock File 4 §4.x).
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from app.core.verdict_codes import VerdictCode

BLHS_2015_D174: Final = "BLHS_2015_D174"  # lừa đảo chiếm đoạt tài sản
BLHS_2015_D201: Final = "BLHS_2015_D201"  # cho vay lãi nặng
NQ_01_2021_HDTP: Final = "NQ_01_2021_HDTP"  # hướng dẫn Điều 201
BLDS_2015_D468: Final = "BLDS_2015_D468"  # trần lãi dân sự
TCTD_2024_D15: Final = "TCTD_2024_D15"  # bảo hiểm không bắt buộc
TCTD_2024_D100: Final = "TCTD_2024_D100"  # lãi suất thỏa thuận của TCTD
TT_43_2016_NHNN: Final = "TT_43_2016_NHNN"  # cho vay tiêu dùng của công ty tài chính
TT_18_2019_NHNN: Final = "TT_18_2019_NHNN"  # quy đổi lãi suất theo năm 365 ngày
ND_94_2025: Final = "ND_94_2025"  # cơ chế thử nghiệm P2P

ALL_LEGAL_BASIS_IDS: Final = frozenset(
    {
        BLHS_2015_D174,
        BLHS_2015_D201,
        NQ_01_2021_HDTP,
        BLDS_2015_D468,
        TCTD_2024_D15,
        TCTD_2024_D100,
        TT_43_2016_NHNN,
        TT_18_2019_NHNN,
        ND_94_2025,
    }
)

LEGAL_BASIS_BY_VERDICT: Final = MappingProxyType(
    {
        VerdictCode.V_RED_ADVANCE_FEE: (BLHS_2015_D174,),
        VerdictCode.V_RED_BRAND_SPOOF: (BLHS_2015_D174, TT_43_2016_NHNN),
        VerdictCode.V_RED_BNPL_OR_STRAW_SCAM: (BLHS_2015_D174,),
        VerdictCode.V_RED_CRIMINAL_USURY: (BLHS_2015_D201, NQ_01_2021_HDTP, BLDS_2015_D468),
        VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED: (BLDS_2015_D468,),
        VerdictCode.V_ORANGE_PAWN_HIGH_FEE: (BLDS_2015_D468, TT_18_2019_NHNN),
        VerdictCode.V_YELLOW_HIGH_COST_LICENSED: (TCTD_2024_D100, TCTD_2024_D15, TT_43_2016_NHNN),
        VerdictCode.V_AMBER_INCOMPLETE_DOC: (TT_18_2019_NHNN,),
        VerdictCode.V_GREEN_TRANSPARENT: (TT_18_2019_NHNN,),
    }
)
