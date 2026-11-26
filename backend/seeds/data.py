"""Dữ liệu seed thuần (không I/O). Mọi mục chưa đối chiếu nguồn chính thức có ``verified=False``.

Quy ước mã căn cứ pháp lý: KHÔNG có tiền tố ``LUAT_`` (khớp ``app/core/mocks.py`` và T09);
File 3 §5.1 viết ``LUAT_TCTD_2024_D100`` -> ở đây là ``TCTD_2024_D100``.
Hotline / packageName / SHA-256 cert / domain: chỉ điền khi đặc tả có; còn lại để rỗng.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Final

SANDBOX_NAMESPACE: Final = "sandbox_seed"
SIMULATED_LABEL: Final = "[Dữ liệu Mô phỏng Thực tiễn]"  # EC-OPS16
P2P_SANDBOX_CAP_VND: Final = 100_000_000  # File 1 §1.2: NĐ 94/2025, trần 100 triệu


def normalize_name(name: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt, gộp khoảng trắng (để so khớp trigram)."""
    decomposed = unicodedata.normalize("NFD", name.replace("đ", "d").replace("Đ", "D"))
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(stripped.lower().split())


@dataclass(frozen=True)
class Lender:
    entity_code: str
    legal_category: str
    short_name: str
    aliases: tuple[str, ...] = ()
    hotlines: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    # mỗi phần tử: {"packageName": str, "signingCertSha256": str | None}
    android_packages: tuple[dict[str, str | None], ...] = ()
    sandbox_cap_vnd: int | None = None
    verified: bool = False

    @property
    def normalized(self) -> str:
        return normalize_name(self.short_name)


# Tên công bố công khai; danh sách + số lượng (đặc tả nói 16 FinCo) CHƯA đối chiếu danh mục
# SBV -> verified=False toàn bộ. Không có package/cert/domain (không bịa).
_BANKS: Final = [
    ("VIETCOMBANK", "Vietcombank"),
    ("VIETINBANK", "VietinBank"),
    ("BIDV", "BIDV"),
    ("AGRIBANK", "Agribank"),
    ("TECHCOMBANK", "Techcombank"),
    ("MBBANK", "MB Bank"),
    ("ACB", "ACB"),
    ("VPBANK", "VPBank"),
    ("SACOMBANK", "Sacombank"),
    ("TPBANK", "TPBank"),
]
_FINCOS: Final[list[tuple[str, str, tuple[str, ...]]]] = [
    ("FE_CREDIT", "FE Credit", ("FECredit",)),
    ("HOME_CREDIT", "Home Credit", ("HomeCredit",)),
    ("HD_SAISON", "HD Saison", ("HDSaison",)),
    ("MCREDIT", "Mcredit", ()),
    ("SHINHAN_FINANCE", "Shinhan Finance", ()),
    ("LOTTE_FINANCE", "Lotte Finance", ()),
    ("MIRAE_ASSET_FINANCE", "Mirae Asset Finance", ("Mirae Asset",)),
    ("TOYOTA_FINANCE", "Toyota Finance", ()),
    ("VIETCREDIT", "Vietcredit", ()),
    ("JACCS", "JACCS", ()),
    ("SHB_FINANCE", "SHB Finance", ("SHBFinance",)),
    ("EASY_CREDIT", "Easy Credit", ("EasyCredit",)),
]


def _build_lenders() -> list[Lender]:
    out: list[Lender] = [Lender(f"BANK_{c}", "COMMERCIAL_BANK", n) for c, n in _BANKS]
    for code, name, aliases in _FINCOS:
        # Hotline duy nhất có trong đặc tả (ví dụ SSE File 4); chưa đối chiếu nguồn chính thức.
        hotlines = ("1900 633 633",) if code == "HOME_CREDIT" else ()
        out.append(Lender(f"FINCO_{code}", "LICENSED_FINCO", name, aliases, hotlines))
    return out


LENDERS: Final[list[Lender]] = _build_lenders()


@dataclass(frozen=True)
class Provision:
    clause_code: str
    doc_type: str
    doc_number: str
    effective_date: str  # ISO; chưa đối chiếu -> verified=False
    title_vi: str
    verbatim_content: str  # rỗng nếu đặc tả không có nguyên văn
    plain_summary_vi: str
    applicable_verdicts: tuple[str, ...]
    verified: bool = False


PROVISIONS: Final[list[Provision]] = [
    Provision(
        "BLDS_2015_D468",
        "LAW",
        "91/2015/QH13",
        "2017-01-01",
        "Điều 468 Bộ luật Dân sự 2015 - Lãi suất vay",
        # Trích từ File 3 §5.1 (đặc tả đã rút gọn): CẦN nguyên văn chính thức.
        "Lãi suất vay do các bên thỏa thuận. Trường hợp các bên có thỏa thuận về lãi suất thì "
        "lãi suất theo thỏa thuận không được vượt quá 20%/năm của khoản tiền vay, trừ trường "
        "hợp luật khác có liên quan quy định khác.",
        "Trần lãi dân sự 20%/năm, trừ khi luật chuyên ngành quy định khác (vd tổ chức tín dụng).",
        ("V_ORANGE_CIVIL_CAP_EXCEEDED", "V_RED_CRIMINAL_USURY", "V_YELLOW_HIGH_COST_LICENSED"),
    ),
    Provision(
        "BLHS_2015_D201",
        "LAW",
        "100/2015/QH13",
        "2018-01-01",
        "Điều 201 Bộ luật Hình sự 2015 - Tội cho vay lãi nặng trong giao dịch dân sự",
        "",
        "Cho vay với lãi suất gấp nhiều lần mức trần dân sự có thể cấu thành tội hình sự.",
        ("V_RED_CRIMINAL_USURY",),
    ),
    Provision(
        "BLHS_2015_D174",
        "LAW",
        "100/2015/QH13",
        "2018-01-01",
        "Điều 174 Bộ luật Hình sự 2015 - Tội lừa đảo chiếm đoạt tài sản",
        "",
        "Dùng thủ đoạn gian dối để chiếm đoạt tài sản (vd thu phí trước, mạo danh) là tội hình sự.",
        ("V_RED_ADVANCE_FEE", "V_RED_BRAND_SPOOF", "V_RED_BNPL_OR_STRAW_SCAM"),
    ),
    Provision(
        "NQ_01_2021_HDTP",
        "RESOLUTION",
        "01/2021/NQ-HĐTP",
        "2021-01-01",
        "Nghị quyết 01/2021/NQ-HĐTP - Hướng dẫn áp dụng Điều 201 BLHS",
        "",
        "Hướng dẫn xác định lãi suất, mức lãi nặng khi áp dụng Điều 201 Bộ luật Hình sự.",
        ("V_RED_CRIMINAL_USURY",),
    ),
    Provision(
        "TCTD_2024_D15",
        "LAW",
        "32/2024/QH15",
        "2024-07-01",
        "Điều 15 Luật Các tổ chức tín dụng 2024 - Hoạt động bị cấm (bảo hiểm gắn với tín dụng)",
        "",
        "Cấm gắn việc cấp tín dụng với việc mua bảo hiểm không bắt buộc (File 1 §1.2).",
        ("V_YELLOW_HIGH_COST_LICENSED",),
    ),
    Provision(
        "TCTD_2024_D100",
        "LAW",
        "32/2024/QH15",
        "2024-07-01",
        "Điều 100 Luật Các tổ chức tín dụng 2024 - Lãi suất, phí trong hoạt động cấp tín dụng",
        "",
        "Tổ chức tín dụng thỏa thuận lãi suất theo quy định chuyên ngành, không bị trần 20% BLDS.",
        ("V_YELLOW_HIGH_COST_LICENSED", "V_GREEN_TRANSPARENT"),
    ),
    Provision(
        "TT_43_2016_NHNN",
        "CIRCULAR",
        "43/2016/TT-NHNN",
        "2017-03-01",
        "Thông tư 43/2016/TT-NHNN - Cho vay tiêu dùng của công ty tài chính",
        "",
        "Quy định minh bạch cho vay tiêu dùng của công ty tài chính.",
        ("V_YELLOW_HIGH_COST_LICENSED", "V_GREEN_TRANSPARENT", "V_AMBER_INCOMPLETE_DOC"),
    ),
    Provision(
        "TT_18_2019_NHNN",
        "CIRCULAR",
        "18/2019/TT-NHNN",
        "2019-12-01",
        "Thông tư 18/2019/TT-NHNN - Sửa đổi Thông tư 43/2016/TT-NHNN",
        "",
        "Sửa đổi, bổ sung quy định cho vay tiêu dùng của công ty tài chính.",
        ("V_YELLOW_HIGH_COST_LICENSED",),
    ),
    Provision(
        "ND_94_2025",
        "DECREE",
        "94/2025/NĐ-CP",
        "2025-01-01",
        "Nghị định 94/2025/NĐ-CP - Cơ chế thử nghiệm có kiểm soát (P2P lending sandbox)",
        "",
        "Khung thử nghiệm P2P; trần giao dịch 100 triệu đồng theo File 1 §1.2.",
        ("V_GREEN_TRANSPARENT", "V_YELLOW_HIGH_COST_LICENSED"),
    ),
]

# (source, target, relation_type) - relation_type theo CHECK của migration 0001.
# Hướng cạnh và quan hệ là giả định (verified=False ở cấp node).
EDGES: Final[list[tuple[str, str, str]]] = [
    ("TCTD_2024_D100", "BLDS_2015_D468", "EXEMPTS_FOR_CREDIT_INST"),  # File 3 §5.1
    ("BLHS_2015_D201", "BLDS_2015_D468", "CRIMINALIZES_EXCESS_OF"),
    ("NQ_01_2021_HDTP", "BLHS_2015_D201", "GUIDES_IMPLEMENTATION"),
    ("TT_18_2019_NHNN", "TT_43_2016_NHNN", "AMENDS"),
    ("TT_43_2016_NHNN", "TCTD_2024_D100", "GUIDES_IMPLEMENTATION"),
]


@dataclass(frozen=True)
class MuleAccount:
    node_id: uuid.UUID
    masked_display: str
    confidence: float
    quorum: int
    hmac: str


@dataclass(frozen=True)
class Cluster:
    cluster_id: uuid.UUID
    label: str
    trap: str
    severity: str
    incidents_24h: int
    impersonated_entity: str | None
    accounts: tuple[MuleAccount, ...]


_NS = uuid.uuid5(uuid.NAMESPACE_URL, "vayshield:sandbox_seed")


def _acct(cluster: str, n: int, conf: float, quorum: int) -> MuleAccount:
    # Tài khoản giả hoàn toàn: HMAC của chuỗi nhãn, không phải STK thật.
    digest = hashlib.sha256(f"{SANDBOX_NAMESPACE}:{cluster}:{n}".encode()).hexdigest()
    return MuleAccount(uuid.uuid5(_NS, f"{cluster}:{n}"), f"****{n:04d}", conf, quorum, digest)


CLUSTERS: Final[list[Cluster]] = [
    Cluster(
        uuid.uuid5(_NS, "cluster:advance_fee"),
        f"{SIMULATED_LABEL} Mạo danh FinCo thu phí mở băng",
        "TRAP_1_ADVANCE_FEE",
        "CRITICAL_WAVE",
        14,
        "FINCO_HOME_CREDIT",
        (_acct("advance_fee", 1, 0.92, 5), _acct("advance_fee", 2, 0.81, 4)),
    ),
    Cluster(
        uuid.uuid5(_NS, "cluster:shark_apk"),
        f"{SIMULATED_LABEL} App vay APK ngoài luồng đòi quyền danh bạ",
        "TRAP_2_SHARK_APK",
        "ELEVATED",
        6,
        None,
        (_acct("shark_apk", 1, 0.66, 3), _acct("shark_apk", 2, 0.58, 3)),
    ),
    Cluster(
        uuid.uuid5(_NS, "cluster:loan_recall"),
        f"{SIMULATED_LABEL} Giả cán bộ thu hồi khoản vay",
        "TRAP_3_LOAN_RECALL",
        "WATCH",
        2,
        None,
        (_acct("loan_recall", 1, 0.40, 2),),
    ),
]
