"""Kiểu dữ liệu dùng chung của contract (enum + model nhỏ).

Quy ước tiền tệ: mọi trường ``*_vnd`` là số nguyên đồng (int), không float.
Quy ước lãi suất: ``apr_*`` / ``ear_xirr`` là phần trăm/năm (vd 501.0 = 501%/năm).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.core.verdict_codes import KernelSignal, Severity, VerdictCode

__all__ = [
    "AmountBucketTier",
    "AudioCallState",
    "CitedClause",
    "HsmState",
    "KernelSignal",
    "LegalCategory",
    "OntologyAction",
    "OntologyAxis",
    "PlatformType",
    "RequestModel",
    "Severity",
    "TriggerOrigin",
    "VerdictCode",
    "VerdictSummary",
    "Vnd",
    "VndPositive",
]

Vnd = Annotated[int, Field(ge=0, le=1_000_000_000_000, description="Số tiền VND (đồng, int)")]
VndPositive = Annotated[int, Field(gt=0, le=1_000_000_000_000, description="Số tiền VND > 0")]


class RequestModel(BaseModel):
    """Model request: cấm trường lạ (giảm bề mặt tấn công)."""

    model_config = ConfigDict(extra="forbid")


class PlatformType(StrEnum):
    ANDROID_NATIVE = "ANDROID_NATIVE"
    WEB_PWA = "WEB_PWA"
    JUDGE_SANDBOX = "JUDGE_SANDBOX"


class HsmState(StrEnum):
    """10 trạng thái HSM (File 2 §2.1)."""

    S0_IDLE = "S0_IDLE"
    S1_LOAN_EXPLORATION = "S1_LOAN_EXPLORATION"
    S2_CONTRACT_AUDITING = "S2_CONTRACT_AUDITING"
    S3_ADVANCE_FEE_HARD_STOP = "S3_ADVANCE_FEE_HARD_STOP"
    S4_SHIELD_ARMED_24H = "S4_SHIELD_ARMED_24H"
    S5_CRITICAL_INTERCEPTION = "S5_CRITICAL_INTERCEPTION"
    S6_COOLING_OFF_LOCK = "S6_COOLING_OFF_LOCK"
    S7_ESCALATED_SCAM_CONFIRMED = "S7_ESCALATED_SCAM_CONFIRMED"
    S8_SAFE_ROUTINE_TX = "S8_SAFE_ROUTINE_TX"
    S9_GOLDEN_HOUR_TRIAGE = "S9_GOLDEN_HOUR_TRIAGE"


class TriggerOrigin(StrEnum):
    """Nguồn kích hoạt phiên bảo vệ (File 4 §2.2 protection_sessions.trigger_origin)."""

    STATION_1_APK = "STATION_1_APK"
    STATION_2_SCAN = "STATION_2_SCAN"
    STATION_2_VOICE = "STATION_2_VOICE"
    STATION_3_NOTIF = "STATION_3_NOTIF"
    B2B_PARTNER_WEBHOOK = "B2B_PARTNER_WEBHOOK"
    SANDBOX_SIM = "SANDBOX_SIM"


class AmountBucketTier(StrEnum):
    """Khoảng số tiền (không lưu số lẻ — Zero-PII). File 4 chỉ nêu TIER_10M_30M; các bậc khác
    là giả định T00."""

    TIER_UNDER_3M = "TIER_UNDER_3M"
    TIER_3M_10M = "TIER_3M_10M"
    TIER_10M_30M = "TIER_10M_30M"
    TIER_30M_100M = "TIER_30M_100M"
    TIER_OVER_100M = "TIER_OVER_100M"


class AudioCallState(StrEnum):
    ACTIVE_CELLULAR = "ACTIVE_CELLULAR"
    ACTIVE_VOIP = "ACTIVE_VOIP"
    ENDED_WITHIN_180S = "ENDED_WITHIN_180S"
    NONE = "NONE"


class OntologyAxis(StrEnum):
    """10 trục thao túng (File 3 §3.3)."""

    C1 = "C1"  # LOAN_REVERSAL_CANCELLATION
    C2 = "C2"  # FREEZE_ERROR_RECTIFICATION
    C3 = "C3"  # CREDIT_SCORE_SOLVENCY_PROOF
    C4 = "C4"  # PSEUDO_OFFICIAL_ACCOUNT_HOP
    C5 = "C5"  # URGENCY_AUTHORITY_DURESS
    C6 = "C6"  # DISBURSEMENT_TAX_FEE_CLAIM
    C7 = "C7"  # COACHED_EVASION_UNDER_CALL
    C8 = "C8"  # BNPL_CASHOUT_MERCHANT_TRAP
    C9 = "C9"  # STRAW_BORROWER_PAYOUT_FORWARD
    C10 = "C10"  # FAKE_POLICE_INVESTIGATION_ESCROW


ONTOLOGY_AXIS_NAMES: dict[OntologyAxis, str] = {
    OntologyAxis.C1: "LOAN_REVERSAL_CANCELLATION",
    OntologyAxis.C2: "FREEZE_ERROR_RECTIFICATION",
    OntologyAxis.C3: "CREDIT_SCORE_SOLVENCY_PROOF",
    OntologyAxis.C4: "PSEUDO_OFFICIAL_ACCOUNT_HOP",
    OntologyAxis.C5: "URGENCY_AUTHORITY_DURESS",
    OntologyAxis.C6: "DISBURSEMENT_TAX_FEE_CLAIM",
    OntologyAxis.C7: "COACHED_EVASION_UNDER_CALL",
    OntologyAxis.C8: "BNPL_CASHOUT_MERCHANT_TRAP",
    OntologyAxis.C9: "STRAW_BORROWER_PAYOUT_FORWARD",
    OntologyAxis.C10: "FAKE_POLICE_INVESTIGATION_ESCROW",
}


class OntologyAction(StrEnum):
    RED = "RED"
    ORANGE_RED = "ORANGE_RED"
    PASS = "PASS"  # noqa: S105 - nhãn hành động, không phải mật khẩu


class LegalCategory(StrEnum):
    COMMERCIAL_BANK = "COMMERCIAL_BANK"
    LICENSED_FINCO = "LICENSED_FINCO"
    P2P_SANDBOX = "P2P_SANDBOX"
    LICENSED_PAWN_CHAIN = "LICENSED_PAWN_CHAIN"
    BNPL_WALLET_PROVIDER = "BNPL_WALLET_PROVIDER"
    UNLICENSED_OR_UNKNOWN = "UNLICENSED_OR_UNKNOWN"


class CitedClause(BaseModel):
    code: str = Field(description="Mã điều khoản, vd BLHS_2015_D174")
    title: str


class VerdictSummary(BaseModel):
    """Kết quả phán quyết tất định (kernel + ma trận 9 nhãn). LLM không sinh trường nào ở đây."""

    verdict_code: VerdictCode
    severity: Severity
    kernel_signal: KernelSignal | None = Field(
        default=None, description="INFINITE_APR_ADVANCE_FEE_SCAM khi phí trước & chưa giải ngân"
    )
    apr_vn_365: float | None = Field(default=None, description="%/năm Actual/365; null nếu ẩn")
    apr_min_bound: float | None = None
    apr_max_bound: float | None = None
    ear_xirr: float | None = Field(default=None, description="%/năm ghép lãi; null nếu ẩn")
    lost_workdays: float | None = Field(default=None, ge=0)
    official_hotline_verify: str | None = None
    contradictions: list[str] = Field(default_factory=list)
    headline_vi: str
    tts_voice_script: str
    cited_clauses: list[CitedClause] = Field(default_factory=list)
