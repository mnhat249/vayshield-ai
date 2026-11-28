"""Gói quy tắc Edge ký Ed25519 (File 4 §4.1 < 40ms, §5.1 mục 3) + ETag.

Mọi dữ liệu lấy từ registry/hằng số đã có — KHÔNG bịa danh sách thật:

- ``lender_whitelist``: ``LenderRecord`` + chữ ký APK từ ``ApkTrustStore``.
- ``banking_app_packages``: gói Android của pháp nhân loại ngân hàng/ví trong registry (rỗng nếu
  registry chưa có gói nào).
- ``blacklist_bloom_b64``: ``None`` (bộ lọc Bloom STK mule chưa có nguồn dữ liệu — xem báo cáo).
- ngưỡng: ``shield_state.risk`` (cam 0.35 / đỏ 0.75), cooling-off 15s (File 4 §2.2 DDL),
  Khiên 24h, cửa sổ smurf 15 phút.

ETag = hash của (schema, key_id, payload chuẩn hóa) nên tự đổi khi registry/ngưỡng đổi. Chữ ký chỉ
phủ ``payload`` (contract đã khóa).
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Final

from ai_pipeline.ontology.challenge import generate_challenge
from app.core.clock import Clock
from app.core.schemas.common import LegalCategory
from app.core.schemas.policies import (
    AndroidPackageSig,
    ChallengeChoiceTemplate,
    ChallengeTemplate,
    PolicyBundle,
    PolicyPayload,
    PolicyThresholds,
    WhitelistedLender,
)
from app.security.signing import Ed25519Signer, canonical_json, sign_policy_bundle
from app.services.audit_pipeline.apk import DANGEROUS_PERMISSION_COMBOS, ApkTrustStore
from app.shield_state.risk import THRESHOLD_ORANGE, THRESHOLD_RED
from app.verdict import LenderRegistry

POLICY_VERSION: Final = 1  # tăng khi đổi SCHEMA gói quy tắc; độ mới nội dung do ETag đảm nhiệm
POLICY_TTL: Final = timedelta(days=7)  # giả định: thiết bị phải đồng bộ lại tối đa 7 ngày/lần
COOLING_OFF_SECONDS: Final = 15
SHIELD_DURATION_HOURS: Final = 24
SMURF_WINDOW_MINUTES: Final = 15
_BANKING_CATEGORIES: Final = frozenset(
    {LegalCategory.COMMERCIAL_BANK, LegalCategory.BNPL_WALLET_PROVIDER}
)


def build_payload(registry: LenderRegistry, trust: ApkTrustStore) -> PolicyPayload:
    whitelist: list[WhitelistedLender] = []
    banking: set[str] = set()
    for record in registry.active_records():
        packages = trust.packages_of(record.entity_code)
        sigs: list[AndroidPackageSig] = []
        for pkg in sorted(set(record.android_packages) | set(packages)):
            certs: list[str | None] = [*sorted(packages.get(pkg, frozenset()))] or [None]
            sigs.extend(AndroidPackageSig(pkg=pkg, sha256=c) for c in certs)
        if record.legal_category in _BANKING_CATEGORIES:
            banking.update(s.pkg for s in sigs)
        whitelist.append(
            WhitelistedLender(
                entity_code=record.entity_code,
                official_short_name=record.official_short_name,
                legal_category=record.legal_category,
                official_hotlines=list(record.official_hotlines),
                official_domains=list(record.official_domains),
                android_packages=sigs,
            )
        )
    template = generate_challenge("policy-template")
    return PolicyPayload(
        lender_whitelist=sorted(whitelist, key=lambda w: w.entity_code),
        banking_app_packages=sorted(banking),
        dangerous_permission_combos=[list(c) for c in DANGEROUS_PERMISSION_COMBOS],
        blacklist_bloom_b64=None,
        thresholds=PolicyThresholds(
            p_scam_watch=THRESHOLD_ORANGE,
            p_scam_cooling_off=THRESHOLD_RED,
            cooling_off_seconds=COOLING_OFF_SECONDS,
            shield_duration_hours=SHIELD_DURATION_HOURS,
            smurf_window_minutes=SMURF_WINDOW_MINUTES,
        ),
        challenge_templates=[
            ChallengeTemplate(
                challenge_id="ch_policy_v1",
                prompt_vi=template.prompt_vi,
                choices=[
                    ChallengeChoiceTemplate(choice_id=c.choice_id, label_vi=c.label_vi)
                    for c in sorted(template.choices, key=lambda c: c.choice_id)
                ],
            )
        ],
    )


def policy_etag(payload: PolicyPayload, key_id: str) -> str:
    digest = hashlib.sha256(
        canonical_json({"v": POLICY_VERSION, "k": key_id, "p": payload.model_dump(mode="json")})
    ).hexdigest()[:24]
    return f'"policy-v{POLICY_VERSION}-{digest}"'


def build_signed_policy(
    registry: LenderRegistry, trust: ApkTrustStore, signer: Ed25519Signer, clock: Clock
) -> tuple[PolicyBundle, str]:
    payload = build_payload(registry, trust)
    bundle = sign_policy_bundle(
        payload,
        policy_version=POLICY_VERSION,
        issued_at=clock.now(),
        ttl=POLICY_TTL,
        signer=signer,
    )
    return bundle, policy_etag(payload, signer.key_id)


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """So khớp ``If-None-Match`` (danh sách, ``*``, tiền tố ``W/`` — so sánh yếu theo RFC 9110)."""
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    wanted = etag.removeprefix("W/")
    return any(c.strip().removeprefix("W/") == wanted for c in if_none_match.split(","))
