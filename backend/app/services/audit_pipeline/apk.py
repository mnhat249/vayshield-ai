"""Trạm 1: kiểm định APK (File 2 §3.1 Tầng 1–3, File 4 §4.1 < 90ms).

Tất định, 0 LLM, hàm thuần trên snapshot registry trong bộ nhớ:

1. ``package_name`` có trong sổ chữ ký chính thức: SHA-256 chứng chỉ khớp → ``OFFICIAL_LICENSED``;
   lệch → ``LOOKALIKE_SPOOF`` (cùng package, khác chữ ký = bản đóng gói lại, S2-EC16).
2. Package lạ nhưng tên hiển thị trùng/nhái thương hiệu đã cấp phép → ``LOOKALIKE_SPOOF``.
3. Còn lại: heuristic tổ hợp quyền nguy hiểm + nguồn cài đặt (Tầng 3) →
   ``UNLICENSED_HIGH_RISK`` (ngoài CH Play + đủ tổ hợp) hoặc ``UNKNOWN_LOW_RISK``.

Giả định an toàn: package có trong registry nhưng chưa có SHA-256 chính thức KHÔNG được coi là
chính thức (tên package không độc quyền khi cài ngoài luồng) → ``UNKNOWN_LOW_RISK`` + gợi ý quét
hợp đồng. Không bao giờ trả "an toàn" cho ứng dụng chưa xác minh được.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.schemas.audits import ApkVerdict, ApkVerifyRequest, ApkVerifyResponse, MatchedLender
from app.core.schemas.common import Severity
from app.verdict import LenderRecord, LenderRegistry, MatchStatus
from app.verdict.lookalike import detect_lookalike_brand

PERMISSION_PREFIX: Final = "android.permission."
# File 2 §3.1 Tầng 3: "APK ngoài luồng đòi READ_CONTACTS + READ_MEDIA_IMAGES". READ_EXTERNAL_STORAGE
# là quyền tương đương của Android < 13 (giả định, cùng ý nghĩa nghiệp vụ).
DANGEROUS_PERMISSION_COMBOS: Final[tuple[tuple[str, ...], ...]] = (
    (PERMISSION_PREFIX + "READ_CONTACTS", PERMISSION_PREFIX + "READ_MEDIA_IMAGES"),
    (PERMISSION_PREFIX + "READ_CONTACTS", PERMISSION_PREFIX + "READ_EXTERNAL_STORAGE"),
)
TRUSTED_INSTALLERS: Final = frozenset({"com.android.vending"})  # Google Play


def normalize_permission(raw: str) -> str:
    value = raw.strip()
    return value if "." in value else PERMISSION_PREFIX + value.upper()


@dataclass(frozen=True, slots=True)
class ApkTrustEntry:
    entity_code: str
    cert_sha256: frozenset[str]


class ApkTrustStore:
    """Ánh xạ ``package_name`` → chữ ký chính thức (``licensed_lender_registry.android_packages``).

    Snapshot bất biến; container thay nguyên khối khi nạp lại (như ``PgLenderRegistry``)."""

    def __init__(self, entries: Mapping[str, ApkTrustEntry] | None = None) -> None:
        self._entries: dict[str, ApkTrustEntry] = dict(entries or {})

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, Any]]) -> ApkTrustStore:
        out: dict[str, ApkTrustEntry] = {}
        for entity_code, raw in rows:
            data = json.loads(raw) if isinstance(raw, str | bytes) else raw
            for item in data if isinstance(data, list) else []:
                if isinstance(item, str):
                    out.setdefault(item, ApkTrustEntry(entity_code, frozenset()))
                    continue
                if not isinstance(item, dict):
                    continue
                pkg = next(
                    (
                        item[k]
                        for k in ("pkg", "package", "package_name", "packageName")
                        if isinstance(item.get(k), str) and item[k]
                    ),
                    None,
                )
                if pkg is None:
                    continue
                sha = next(
                    (
                        item[k]
                        for k in ("sha256", "signingCertSha256", "signing_cert_sha256")
                        if isinstance(item.get(k), str) and item[k]
                    ),
                    None,
                )
                prev = out.get(pkg)
                certs = set(prev.cert_sha256) if prev else set()
                if sha:
                    certs.add(sha.lower())
                out[pkg] = ApkTrustEntry(entity_code, frozenset(certs))
        return cls(out)

    def get(self, package_name: str) -> ApkTrustEntry | None:
        return self._entries.get(package_name)

    def packages_of(self, entity_code: str) -> dict[str, frozenset[str]]:
        return {p: e.cert_sha256 for p, e in self._entries.items() if e.entity_code == entity_code}

    def __len__(self) -> int:
        return len(self._entries)


async def load_apk_trust(engine: AsyncEngine) -> ApkTrustStore:
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT entity_code, android_packages FROM licensed_lender_registry WHERE is_active"
            )
        )
        return ApkTrustStore.from_rows((str(r[0]), r[1]) for r in result)


@dataclass(frozen=True, slots=True)
class ApkOutcome:
    response: ApkVerifyResponse
    signature_ok: bool | None  # đưa vào Provenance của Trạm 2 khi liên kết audit
    package_name: str


def _matched(record: LenderRecord) -> MatchedLender:
    return MatchedLender(
        entity_code=record.entity_code,
        official_short_name=record.official_short_name,
        legal_category=record.legal_category,
        official_hotlines=list(record.official_hotlines),
    )


def dangerous_permissions(requested: Iterable[str]) -> list[str]:
    have = {normalize_permission(p) for p in requested}
    hit: set[str] = set()
    for combo in DANGEROUS_PERMISSION_COMBOS:
        if all(p in have for p in combo):
            hit.update(combo)
    return sorted(hit)


def verify_apk(
    req: ApkVerifyRequest, registry: LenderRegistry, trust: ApkTrustStore, audit_id: Any
) -> ApkOutcome:
    cert = req.signing_cert_sha256.lower()
    dangerous = dangerous_permissions(req.requested_permissions)
    sideloaded = req.installer_package not in TRUSTED_INSTALLERS
    entry = trust.get(req.package_name)
    record = registry.get(entry.entity_code) if entry is not None else None
    reasons: list[str] = []

    def build(
        verdict: ApkVerdict,
        severity: Severity,
        *,
        matched: LenderRecord | None = None,
        lookalike_of: str | None = None,
        signature_ok: bool | None = None,
    ) -> ApkOutcome:
        return ApkOutcome(
            ApkVerifyResponse(
                audit_id=audit_id,
                apk_verdict=verdict,
                severity=severity,
                matched_lender=_matched(matched) if matched is not None else None,
                lookalike_of=lookalike_of,
                dangerous_permissions=dangerous,
                reasons_vi=reasons,
                recommend_contract_scan=verdict is not ApkVerdict.OFFICIAL_LICENSED,
            ),
            signature_ok,
            req.package_name,
        )

    if entry is not None and record is not None:
        if cert in entry.cert_sha256:
            reasons.append(f"Chữ ký ứng dụng khớp bản chính thức của {record.official_short_name}.")
            return build(
                ApkVerdict.OFFICIAL_LICENSED, Severity.GREEN, matched=record, signature_ok=True
            )
        if entry.cert_sha256:
            reasons.append(
                f"Ứng dụng mang tên/gói của {record.official_short_name} nhưng chữ ký số KHÔNG "
                "khớp bản chính thức — có thể là bản giả mạo đã đóng gói lại."
            )
            return build(
                ApkVerdict.LOOKALIKE_SPOOF,
                Severity.CRITICAL_RED,
                lookalike_of=record.official_short_name,
                signature_ok=False,
            )
        reasons.append(
            "Chưa có chữ ký chính thức để đối chiếu; không thể xác nhận đây là ứng dụng thật."
        )
        return build(ApkVerdict.UNKNOWN_LOW_RISK, Severity.AMBER, signature_ok=None)

    claimed = detect_lookalike_brand(req.app_label, registry) if req.app_label else None
    if (
        claimed is not None
        and claimed.record is not None
        and any(trust.packages_of(claimed.record.entity_code).values())  # biết chứng chỉ chính thức
        and claimed.status
        in (
            MatchStatus.EXACT,
            MatchStatus.LOOKALIKE,
        )
    ):
        reasons.append(
            f"Ứng dụng mang tên giống {claimed.record.official_short_name} nhưng không phải gói "
            "phát hành chính thức."
        )
        return build(
            ApkVerdict.LOOKALIKE_SPOOF,
            Severity.CRITICAL_RED,
            lookalike_of=claimed.record.official_short_name,
            signature_ok=False,
        )

    if sideloaded and dangerous:
        reasons.append(
            "Cài ngoài cửa hàng ứng dụng và đòi đọc danh bạ cùng ảnh trong máy — "
            "dấu hiệu của app vay trá hình."
        )
        return build(ApkVerdict.UNLICENSED_HIGH_RISK, Severity.RED)
    if dangerous:
        reasons.append("Ứng dụng chưa xác minh đòi quyền đọc danh bạ cùng ảnh trong máy.")
    elif sideloaded:
        reasons.append("Ứng dụng cài ngoài cửa hàng ứng dụng, chưa có trong danh sách cấp phép.")
    else:
        reasons.append("Ứng dụng chưa có trong danh sách tổ chức được cấp phép.")
    return build(ApkVerdict.UNKNOWN_LOW_RISK, Severity.AMBER)
