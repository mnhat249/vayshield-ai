"""Chấm điểm rủi ro Bayesian log-odds đa phiên (File 2 §2.2).

Hàm thuần: KHÔNG đọc đồng hồ hệ thống, KHÔNG I/O. Mọi thời điểm là ``*_ms`` (mili-giây, thang thời
gian không giảm do caller cung cấp — xem ``app.core.clock``).

    L_j(t)   = L_prior + Σ w_k·decay_k(t − t_k) + Ψ_synergy(t) + Ξ_proj(t) + Ω_smurf(t)
    L_eff(t) = max_j L_j(t) + γ_multi · ln|F_active(t)|
    P(Scam)  = 1 / (1 + e^(−L_eff))

Ψ, Ξ, Ω không phụ thuộc chỉ số j (thuộc cấp thiết bị) nên ``max_j(a_j + c) = max_j(a_j) + c``: có
thể tách phần "nền" (prior + bằng chứng + γ) khỏi phần cảm biến tức thời (Lua cộng Ψ/Ξ/Ω).

Log-odds không phải tiền tệ → dùng ``float`` (CLAUDE.md §4 chỉ cấm float cho tiền).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.core.schemas.common import AudioCallState

MINUTE_MS: Final = 60_000
HOUR_MS: Final = 60 * MINUTE_MS

# --- Hằng số File 2 §2.2 -------------------------------------------------------------------
L_PRIOR: Final = -3.5
GAMMA_MULTI: Final = 0.55

W_SPOOF: Final = 4.2  # cảnh báo giả mạo / phí ứng trước / rút tiền Ví trả sau (Trạm 2)
TAU_SPOOF_MS: Final = 4 * HOUR_MS
W_DISB: Final = 2.8  # tiền giải ngân về tài khoản
DISB_PLATEAU_MS: Final = 120 * MINUTE_MS  # 120 phút vàng: giữ đỉnh, không suy giảm
TAU_DISB_MS: Final = 12 * HOUR_MS
W_APK: Final = 1.8  # cài APK vay ngoài CH Play (Trạm 1)
TAU_APK_MS: Final = 48 * HOUR_MS

PSI_ACTIVE_CALL: Final = 3.8
PSI_RECENT_CALL: Final = 1.9
RECENT_CALL_WINDOW_MS: Final = 180_000  # S2-EC02: dư chấn 180 giây sau khi cúp máy
XI_PROJECTION: Final = 4.5
OMEGA_STEP: Final = 0.9
SMURF_WINDOW_MS: Final = 15 * MINUTE_MS

THRESHOLD_ORANGE: Final = 0.35
THRESHOLD_RED: Final = 0.75

# Giả định (đặc tả không định nghĩa DisbursementActive): khoản giải ngân còn "hoạt động" trong
# suốt cửa sổ Khiên 24h kể từ lúc tiền về (File 2 §2.1 S4_SHIELD_ARMED_24H).
DISBURSEMENT_ACTIVE_MS: Final = 24 * HOUR_MS


class EvidenceKind(StrEnum):
    SPOOF_OR_ADVANCE_FEE = "SPOOF_OR_ADVANCE_FEE"
    DISBURSEMENT = "DISBURSEMENT"
    OFFSTORE_APK = "OFFSTORE_APK"


class AudioMode(StrEnum):
    NORMAL = "NORMAL"
    IN_CALL = "IN_CALL"
    IN_COMM = "IN_COMM"


class RiskTier(StrEnum):
    PASS_SILENT = "PASS_SILENT"  # noqa: S105 - nhãn mức rủi ro (P < 0.35), không phải mật khẩu
    ORANGE_SOFT_CHECK = "ORANGE_SOFT_CHECK"  # 0.35 ≤ P < 0.75
    RED_HARD_INTERCEPTION = "RED_HARD_INTERCEPTION"  # P ≥ 0.75


_WEIGHTS: Final[dict[EvidenceKind, float]] = {
    EvidenceKind.SPOOF_OR_ADVANCE_FEE: W_SPOOF,
    EvidenceKind.DISBURSEMENT: W_DISB,
    EvidenceKind.OFFSTORE_APK: W_APK,
}


@dataclass(frozen=True, slots=True)
class Evidence:
    """Một sự kiện lịch sử e_k trong H_{j,t}."""

    kind: EvidenceKind
    at_ms: int
    event_id: str


@dataclass(frozen=True, slots=True)
class SensorContext:
    """Cảm biến tức thời cấp thiết bị tại thời điểm đánh giá."""

    audio_mode: AudioMode = AudioMode.NORMAL
    ms_since_call_end: int | None = None
    media_projection_active: bool = False
    remote_control_active: bool = False
    bank_open_count_15m: int = 0


@dataclass(frozen=True, slots=True)
class RiskBreakdown:
    prior: float
    evidence: float
    psi_synergy: float
    xi_proj: float
    omega_smurf: float

    @property
    def total(self) -> float:
        return self.prior + self.evidence + self.psi_synergy + self.xi_proj + self.omega_smurf


def _half_life_decay(age_ms: int, tau_ms: int) -> float:
    return math.exp(-(math.log(2) / tau_ms) * age_ms)


def evidence_contribution(kind: EvidenceKind, age_ms: int) -> float:
    """w_k · decay(t − t_k). Tuổi âm (đồng hồ lệch / sự kiện "tương lai") kẹp về 0 → đủ trọng số
    (phương án an toàn: không để lệch đồng hồ làm giảm rủi ro)."""
    age = max(0, age_ms)
    weight = _WEIGHTS[kind]
    if kind is EvidenceKind.DISBURSEMENT:
        # Hàm hai pha: giữ đỉnh trong 120 phút vàng, sau đó bán rã τ = 12h tính từ cuối pha 1.
        if age <= DISB_PLATEAU_MS:
            return weight
        return weight * _half_life_decay(age - DISB_PLATEAU_MS, TAU_DISB_MS)
    tau = TAU_SPOOF_MS if kind is EvidenceKind.SPOOF_OR_ADVANCE_FEE else TAU_APK_MS
    return weight * _half_life_decay(age, tau)


def evidence_log_odds(evidence: Iterable[Evidence], now_ms: int) -> float:
    """Σ w_k · decay_k (không gồm prior)."""
    return sum(evidence_contribution(e.kind, now_ms - e.at_ms) for e in evidence)


def disbursement_active(evidence: Iterable[Evidence], now_ms: int) -> bool:
    return any(
        e.kind is EvidenceKind.DISBURSEMENT and now_ms - e.at_ms <= DISBURSEMENT_ACTIVE_MS
        for e in evidence
    )


def in_golden_window(evidence: Iterable[Evidence], now_ms: int) -> bool:
    """Trong 120 phút vàng sau giải ngân (S2-EC31)."""
    return any(
        e.kind is EvidenceKind.DISBURSEMENT and now_ms - e.at_ms <= DISB_PLATEAU_MS
        for e in evidence
    )


def call_synergy(
    *, disbursement_is_active: bool, audio_mode: AudioMode, ms_since_call_end: int | None
) -> float:
    """Ψ_synergy: +3.8 đang gọi, +1.9 nếu vừa cúp máy ≤ 180s, 0 nếu không có giải ngân."""
    if not disbursement_is_active:
        return 0.0
    if audio_mode in (AudioMode.IN_CALL, AudioMode.IN_COMM):
        return PSI_ACTIVE_CALL
    if ms_since_call_end is not None and 0 <= ms_since_call_end <= RECENT_CALL_WINDOW_MS:
        return PSI_RECENT_CALL
    return 0.0


def projection_term(*, media_projection_active: bool, remote_control_active: bool) -> float:
    """Ξ_proj: +4.5 khi chia sẻ màn hình (MediaProjection FGS) hoặc app điều khiển từ xa."""
    return XI_PROJECTION if (media_projection_active or remote_control_active) else 0.0


def smurf_term(open_count_15m: int) -> float:
    """Ω_smurf = 0.9 · max(0, m_15m − 1)."""
    return OMEGA_STEP * max(0, open_count_15m - 1)


def sensor_log_odds(sensors: SensorContext, *, disbursement_is_active: bool) -> float:
    """Ψ + Ξ + Ω (phần tức thời cấp thiết bị)."""
    return (
        call_synergy(
            disbursement_is_active=disbursement_is_active,
            audio_mode=sensors.audio_mode,
            ms_since_call_end=sensors.ms_since_call_end,
        )
        + projection_term(
            media_projection_active=sensors.media_projection_active,
            remote_control_active=sensors.remote_control_active,
        )
        + smurf_term(sensors.bank_open_count_15m)
    )


def explain_log_odds(
    evidence: Sequence[Evidence],
    now_ms: int,
    sensors: SensorContext | None = None,
    *,
    disbursement_is_active: bool | None = None,
) -> RiskBreakdown:
    """Phân rã L_j(t). ``disbursement_is_active`` mặc định suy từ chính bằng chứng của luồng;
    cấp thiết bị truyền giá trị gộp của mọi luồng."""
    s = sensors or SensorContext()
    active = (
        disbursement_active(evidence, now_ms)
        if disbursement_is_active is None
        else disbursement_is_active
    )
    return RiskBreakdown(
        prior=L_PRIOR,
        evidence=evidence_log_odds(evidence, now_ms),
        psi_synergy=call_synergy(
            disbursement_is_active=active,
            audio_mode=s.audio_mode,
            ms_since_call_end=s.ms_since_call_end,
        ),
        xi_proj=projection_term(
            media_projection_active=s.media_projection_active,
            remote_control_active=s.remote_control_active,
        ),
        omega_smurf=smurf_term(s.bank_open_count_15m),
    )


def compute_log_odds(
    evidence: Sequence[Evidence],
    now_ms: int,
    sensors: SensorContext | None = None,
    *,
    disbursement_is_active: bool | None = None,
) -> float:
    """L_j(t) đúng công thức File 2 §2.2."""
    return explain_log_odds(
        evidence, now_ms, sensors, disbursement_is_active=disbursement_is_active
    ).total


def compute_effective_log_odds(fiber_log_odds: Sequence[float]) -> float:
    """L_eff = max_j L_j + γ_multi · ln|F_active|. Không có luồng hoạt động → L_prior."""
    if not fiber_log_odds:
        return L_PRIOR
    return max(fiber_log_odds) + GAMMA_MULTI * math.log(len(fiber_log_odds))


def scam_probability(log_odds: float) -> float:
    """Sigmoid ổn định số học."""
    if log_odds >= 0:
        return 1.0 / (1.0 + math.exp(-log_odds))
    z = math.exp(log_odds)
    return z / (1.0 + z)


def risk_tier(probability: float, *, golden_hour_floor: bool = False) -> RiskTier:
    """Ngưỡng 0.35 / 0.75. ``golden_hour_floor`` (S2-EC31): trong 120 phút vàng sau giải ngân mà
    mở app ngân hàng thì tối thiểu là Mức Cam — kể cả khi máy chính báo MODE_NORMAL."""
    if probability >= THRESHOLD_RED:
        return RiskTier.RED_HARD_INTERCEPTION
    if probability >= THRESHOLD_ORANGE or golden_hour_floor:
        return RiskTier.ORANGE_SOFT_CHECK
    return RiskTier.PASS_SILENT


def sensors_from_call_state(
    call_state: AudioCallState,
    *,
    media_projection_active: bool = False,
    remote_control_active: bool = False,
    bank_open_count_15m: int = 0,
) -> SensorContext:
    """Ánh xạ ``AudioCallState`` (contract) → ``SensorContext``."""
    mode = AudioMode.NORMAL
    since_end: int | None = None
    if call_state is AudioCallState.ACTIVE_CELLULAR:
        mode = AudioMode.IN_CALL
    elif call_state is AudioCallState.ACTIVE_VOIP:
        mode = AudioMode.IN_COMM
    elif call_state is AudioCallState.ENDED_WITHIN_180S:
        since_end = 0  # thiết bị chỉ báo "≤ 180s" → coi như trong cửa sổ dư chấn
    return SensorContext(
        audio_mode=mode,
        ms_since_call_end=since_end,
        media_projection_active=media_projection_active,
        remote_control_active=remote_control_active,
        bank_open_count_15m=bank_open_count_15m,
    )
