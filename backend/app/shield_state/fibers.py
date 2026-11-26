"""Bộ giám sát đa luồng vay ``FiberSupervisor`` (K = 8) + toán tử join-semilattice (File 2 §2.1).

Thuần (không I/O, không đọc đồng hồ — ngoại lệ duy nhất: ``_default_session_id`` sinh UUIDv7, xem
docstring của nó). Lớp Redis (``store.py``) đọc bản ghi → dựng supervisor → áp thao tác → ghi lại
bằng CAS (EC-DB07).

Quy tắc cấp supervisor (ngoài bảng ``hsm.TRANSITIONS``, đều là giả định an toàn):

- Tiền về (E3/E4/E5) ở S1 → ánh xạ thành E3 (guard 72h); ở S2 → S4 (Khiên 24h, "24h on Credit");
  ở S0 chỉ E4/E5 được nhận, E3 (tín hiệu tín dụng độ tin cậy thấp, chưa có hành trình vay) bị từ
  chối rõ ràng — không tạo luồng nửa vời. Ở S3..S9: giữ trạng thái, gia hạn Khiên theo MAX.
- S8 tự quay về S4 ("Resume remaining watch") khi nhận sự kiện không có trong bảng (vd E6 mở lại
  app ngân hàng) và Khiên còn hạn. S7/S9 về S4 qua sự kiện tường minh ``A_*`` của caller.
- ``expire()`` dọn mọi trạng thái: S1 sau 72h (T1); S2 sau 4h không tiến triển; S3 sau cửa sổ 4h
  → S4 (giữ bằng chứng + Khiên tối thiểu 24h kể từ lúc vào S3, an toàn hơn xóa bỏ); S4 khi hết
  hạn Khiên (T2); S5..S9 khi hết hạn Khiên (thiếu hạn → 24h kể từ lúc vào trạng thái).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from app.core.ids import new_uuid7
from app.core.schemas.common import HsmState, TriggerOrigin
from app.shield_state.hsm import (
    GLOBAL_TRANSITIONS,
    HARD_STOP_BANK_WINDOW_MS,
    SHIELD_ARM_MS,
    SHIELD_DEFAULT_MS,
    STATE_SEVERITY,
    TRANSITIONS,
    HsmEvent,
    TransitionOutcome,
    join_state,
    transition,
)
from app.shield_state.risk import (
    MINUTE_MS,
    Evidence,
    EvidenceKind,
    RiskTier,
    SensorContext,
    compute_effective_log_odds,
    compute_log_odds,
    disbursement_active,
    in_golden_window,
    risk_tier,
    scam_probability,
)

K_MAX_FIBERS: Final = 8
DEFAULT_FIBER_KEY: Final = "default"
RACE_MERGE_WINDOW_MS: Final = 30 * MINUTE_MS  # S2-EC43: khóa phiên (device_id, time_window_30m)
MAX_EVIDENCE_PER_FIBER: Final = 32
AUDIT_IDLE_TTL_MS: Final = HARD_STOP_BANK_WINDOW_MS  # S2 không tiến triển sau 4h → dọn
# Trạng thái mà tiền về chỉ gia hạn Khiên (không đổi trạng thái).
_EXTEND_ONLY_STATES: Final = frozenset(
    {
        HsmState.S3_ADVANCE_FEE_HARD_STOP,
        HsmState.S4_SHIELD_ARMED_24H,
        HsmState.S5_CRITICAL_INTERCEPTION,
        HsmState.S6_COOLING_OFF_LOCK,
        HsmState.S7_ESCALATED_SCAM_CONFIRMED,
        HsmState.S8_SAFE_ROUTINE_TX,
        HsmState.S9_GOLDEN_HOUR_TRIAGE,
    }
)
# Trạng thái hậu-chặn: dọn khi hết hạn Khiên.
_POST_INTERCEPT_STATES: Final = frozenset(
    {
        HsmState.S5_CRITICAL_INTERCEPTION,
        HsmState.S6_COOLING_OFF_LOCK,
        HsmState.S7_ESCALATED_SCAM_CONFIRMED,
        HsmState.S8_SAFE_ROUTINE_TX,
        HsmState.S9_GOLDEN_HOUR_TRIAGE,
    }
)


class EvidenceSource(StrEnum):
    B2B_CONFIRMED = "B2B_CONFIRMED"
    EDGE_NOTIF = "EDGE_NOTIF"
    STATION_1_APK = "STATION_1_APK"
    STATION_2_SCAN = "STATION_2_SCAN"
    STATION_2_VOICE = "STATION_2_VOICE"
    SANDBOX_SIM = "SANDBOX_SIM"


SOURCE_FROM_ORIGIN: Final[Mapping[TriggerOrigin, EvidenceSource]] = {
    TriggerOrigin.STATION_1_APK: EvidenceSource.STATION_1_APK,
    TriggerOrigin.STATION_2_SCAN: EvidenceSource.STATION_2_SCAN,
    TriggerOrigin.STATION_2_VOICE: EvidenceSource.STATION_2_VOICE,
    TriggerOrigin.STATION_3_NOTIF: EvidenceSource.EDGE_NOTIF,
    TriggerOrigin.B2B_PARTNER_WEBHOOK: EvidenceSource.B2B_CONFIRMED,
    TriggerOrigin.SANDBOX_SIM: EvidenceSource.SANDBOX_SIM,
}

EVENT_EVIDENCE: Final[Mapping[HsmEvent, EvidenceKind]] = {
    HsmEvent.E1_APK_INSTALLED: EvidenceKind.OFFSTORE_APK,
    HsmEvent.V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT: EvidenceKind.SPOOF_OR_ADVANCE_FEE,
    HsmEvent.E3_ANY_ORG_CREDIT_INFLOW: EvidenceKind.DISBURSEMENT,
    HsmEvent.E4_HIGH_CONF_DISBURSEMENT_NOTIF: EvidenceKind.DISBURSEMENT,
    HsmEvent.E5_B2B_WEBHOOK: EvidenceKind.DISBURSEMENT,
}

DISBURSEMENT_EVENTS: Final = frozenset(
    {
        HsmEvent.E3_ANY_ORG_CREDIT_INFLOW,
        HsmEvent.E4_HIGH_CONF_DISBURSEMENT_NOTIF,
        HsmEvent.E5_B2B_WEBHOOK,
    }
)


def _evidence_sort_key(e: Evidence) -> tuple[int, str, str]:
    return (e.at_ms, e.event_id, e.kind.value)


def _merge_evidence(*groups: Iterable[Evidence]) -> tuple[Evidence, ...]:
    """Hợp theo event_id (trùng id → chọn bản lớn nhất theo thứ tự tất định), giữ N mới nhất."""
    by_id: dict[str, Evidence] = {}
    for group in groups:
        for e in group:
            cur = by_id.get(e.event_id)
            if cur is None or _evidence_sort_key(e) > _evidence_sort_key(cur):
                by_id[e.event_id] = e
    newest = sorted(by_id.values(), key=_evidence_sort_key, reverse=True)
    return tuple(sorted(newest[:MAX_EVIDENCE_PER_FIBER], key=_evidence_sort_key))


def _max_opt(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


@dataclass(frozen=True, slots=True)
class LoanJourneyFiber:
    fiber_key: str
    session_id: str
    state: HsmState
    entered_at_ms: int
    updated_at_ms: int
    shield_expires_at_ms: int | None = None
    evidence: tuple[Evidence, ...] = ()
    evidence_sources: frozenset[EvidenceSource] = field(default_factory=frozenset)

    @property
    def latest_disbursement_ms(self) -> int | None:
        times = [e.at_ms for e in self.evidence if e.kind is EvidenceKind.DISBURSEMENT]
        return max(times) if times else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fiber_key": self.fiber_key,
            "session_id": self.session_id,
            "state": self.state.value,
            "entered_at_ms": self.entered_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "shield_expires_at_ms": self.shield_expires_at_ms,
            "evidence": [[e.kind.value, e.at_ms, e.event_id] for e in self.evidence],
            "evidence_sources": sorted(s.value for s in self.evidence_sources),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LoanJourneyFiber:
        exp = data.get("shield_expires_at_ms")
        return cls(
            fiber_key=str(data["fiber_key"]),
            session_id=str(data["session_id"]),
            state=HsmState(data["state"]),
            entered_at_ms=int(data["entered_at_ms"]),
            updated_at_ms=int(data["updated_at_ms"]),
            shield_expires_at_ms=None if exp is None else int(exp),
            evidence=_merge_evidence(
                Evidence(EvidenceKind(k), int(t), str(i)) for k, t, i in data.get("evidence", [])
            ),
            evidence_sources=frozenset(EvidenceSource(s) for s in data.get("evidence_sources", [])),
        )


def join_fibers(a: LoanJourneyFiber, b: LoanJourneyFiber) -> LoanJourneyFiber:
    """a ⊔ b cho 2 bản của CÙNG một luồng: trạng thái nghiêm trọng hơn, MAX(shield_expires_at),
    hợp bằng chứng + nguồn bằng chứng. Giao hoán, kết hợp, lũy đẳng."""
    if a.fiber_key != b.fiber_key:
        raise ValueError("join_fibers yêu cầu cùng fiber_key")
    state = join_state(a.state, b.state)
    if a.state is b.state:
        entered = min(a.entered_at_ms, b.entered_at_ms)
    elif state is a.state:
        entered = a.entered_at_ms
    else:
        entered = b.entered_at_ms
    return LoanJourneyFiber(
        fiber_key=a.fiber_key,
        session_id=min(a.session_id, b.session_id),
        state=state,
        entered_at_ms=entered,
        updated_at_ms=max(a.updated_at_ms, b.updated_at_ms),
        shield_expires_at_ms=_max_opt(a.shield_expires_at_ms, b.shield_expires_at_ms),
        evidence=_merge_evidence(a.evidence, b.evidence),
        evidence_sources=a.evidence_sources | b.evidence_sources,
    )


@dataclass(frozen=True, slots=True)
class RiskEvaluation:
    log_odds: float
    probability: float
    tier: RiskTier
    active_fiber_count: int
    disbursement_active: bool
    golden_window: bool


def _default_session_id() -> str:
    """Ngoại lệ có chủ đích với quy tắc "không đọc đồng hồ": UUIDv7 nhúng thời gian để sắp xếp
    khóa phiên, KHÔNG tham gia tính rủi ro/hết hạn (mọi phép tính thời gian dùng ``now_ms``).
    Code cần tất định (test, replay) truyền ``new_session_id`` vào ``FiberSupervisor``."""
    return str(new_uuid7())


class FiberSupervisor:
    """Tập tối đa K luồng ``LoanJourneyFiber`` của một thiết bị. Luồng ở S0 bị loại bỏ."""

    def __init__(
        self,
        fibers: Iterable[LoanJourneyFiber] = (),
        *,
        k_max: int = K_MAX_FIBERS,
        new_session_id: Callable[[], str] = _default_session_id,
    ) -> None:
        self._k_max = k_max
        self._new_session_id = new_session_id
        self._fibers: dict[str, LoanJourneyFiber] = {}
        for f in fibers:
            cur = self._fibers.get(f.fiber_key)
            self._fibers[f.fiber_key] = f if cur is None else join_fibers(cur, f)

    # --- Truy vấn ---------------------------------------------------------------------------
    @property
    def fibers(self) -> tuple[LoanJourneyFiber, ...]:
        return tuple(self._fibers[k] for k in sorted(self._fibers))

    def get(self, fiber_key: str) -> LoanJourneyFiber | None:
        return self._fibers.get(fiber_key)

    def by_session(self, session_id: str) -> LoanJourneyFiber | None:
        return next((f for f in self._fibers.values() if f.session_id == session_id), None)

    def active_fibers(self) -> tuple[LoanJourneyFiber, ...]:
        return tuple(f for f in self.fibers if f.state is not HsmState.S0_IDLE)

    def device_state(self) -> HsmState:
        state = HsmState.S0_IDLE
        for f in self._fibers.values():
            state = join_state(state, f.state)
        return state

    def shield_expires_at_ms(self) -> int | None:
        out: int | None = None
        for f in self._fibers.values():
            out = _max_opt(out, f.shield_expires_at_ms)
        return out

    def latest_disbursement_ms(self) -> int | None:
        out: int | None = None
        for f in self._fibers.values():
            out = _max_opt(out, f.latest_disbursement_ms)
        return out

    def _all_evidence(self) -> list[Evidence]:
        return [e for f in self.active_fibers() for e in f.evidence]

    # --- Chấm điểm --------------------------------------------------------------------------
    def fiber_log_odds(self, now_ms: int, sensors: SensorContext) -> dict[str, float]:
        disb = disbursement_active(self._all_evidence(), now_ms)
        return {
            f.fiber_key: compute_log_odds(f.evidence, now_ms, sensors, disbursement_is_active=disb)
            for f in self.active_fibers()
        }

    def base_log_odds(self, now_ms: int) -> float:
        """max_j(L_prior + Σ bằng chứng) + γ·ln|F| — phần nền đưa vào Lua (chưa gồm Ψ/Ξ/Ω)."""
        return compute_effective_log_odds(
            list(self.fiber_log_odds(now_ms, SensorContext()).values())
        )

    def evaluate(self, now_ms: int, sensors: SensorContext) -> RiskEvaluation:
        evidence = self._all_evidence()
        per_fiber = self.fiber_log_odds(now_ms, sensors)
        if per_fiber:
            lo = compute_effective_log_odds(list(per_fiber.values()))
        else:
            lo = compute_log_odds([], now_ms, sensors)  # vẫn tính Ξ/Ω khi chưa có luồng nào
        golden = in_golden_window(evidence, now_ms)
        p = scam_probability(lo)
        return RiskEvaluation(
            log_odds=lo,
            probability=p,
            tier=risk_tier(p, golden_hour_floor=golden),
            active_fiber_count=len(per_fiber),
            disbursement_active=disbursement_active(evidence, now_ms),
            golden_window=golden,
        )

    # --- Thao tác ---------------------------------------------------------------------------
    def _store(self, fiber: LoanJourneyFiber) -> None:
        if fiber.state is HsmState.S0_IDLE:
            self._fibers.pop(fiber.fiber_key, None)
            return
        if fiber.fiber_key in self._fibers or len(self._fibers) < self._k_max:
            self._fibers[fiber.fiber_key] = fiber
            return
        # Đầy K luồng: gộp (join) vào luồng ít nghiêm trọng + cũ nhất — join không bao giờ làm
        # mất Khiên/bằng chứng (an toàn hơn loại bỏ).
        victim = min(
            self._fibers.values(),
            key=lambda f: (STATE_SEVERITY[f.state], f.updated_at_ms, f.fiber_key),
        )
        self._fibers[victim.fiber_key] = join_fibers(
            victim, dataclasses.replace(fiber, fiber_key=victim.fiber_key)
        )

    @staticmethod
    def _shield_horizon_ms(f: LoanJourneyFiber) -> int:
        """Hạn Khiên của luồng; thiếu → 24h kể từ lúc vào trạng thái hiện tại."""
        if f.shield_expires_at_ms is not None:
            return f.shield_expires_at_ms
        return f.entered_at_ms + SHIELD_DEFAULT_MS

    @staticmethod
    def _expire_one(
        f: LoanJourneyFiber, now_ms: int
    ) -> tuple[LoanJourneyFiber, TransitionOutcome | None]:
        """Áp hết hạn cho MỘT luồng. Trả về luồng mới (state S0 = cần dọn) + outcome nếu có đổi."""
        state = f.state
        if state is HsmState.S1_LOAN_EXPLORATION:
            out = transition(
                state,
                HsmEvent.T1_MONOTONIC_TTL_EXPIRED,
                now_ms=now_ms,
                entered_at_ms=f.entered_at_ms,
                shield_expires_at_ms=f.shield_expires_at_ms,
            )
            if out.accepted:
                return dataclasses.replace(f, state=out.to_state, updated_at_ms=now_ms), out
            return f, None
        if state is HsmState.S2_CONTRACT_AUDITING:
            if now_ms - f.entered_at_ms >= AUDIT_IDLE_TTL_MS:
                out = TransitionOutcome(
                    state, HsmEvent.T1_MONOTONIC_TTL_EXPIRED, HsmState.S0_IDLE, accepted=True
                )
                return dataclasses.replace(f, state=HsmState.S0_IDLE, updated_at_ms=now_ms), out
            return f, None
        resumed: TransitionOutcome | None = None
        if state is HsmState.S3_ADVANCE_FEE_HARD_STOP:
            if now_ms - f.entered_at_ms <= HARD_STOP_BANK_WINDOW_MS:
                return f, None
            # Hết cửa sổ 4h của S3 → S4 (vẫn theo dõi, giữ bằng chứng spoof), Khiên ≥ 24h từ S3.
            shield = _max_opt(f.shield_expires_at_ms, f.entered_at_ms + SHIELD_DEFAULT_MS)
            f = dataclasses.replace(
                f,
                state=HsmState.S4_SHIELD_ARMED_24H,
                entered_at_ms=now_ms,
                updated_at_ms=max(f.updated_at_ms, now_ms),
                shield_expires_at_ms=shield,
            )
            resumed = TransitionOutcome(
                state, HsmEvent.T1_MONOTONIC_TTL_EXPIRED, f.state, accepted=True
            )
            state = f.state
        if state is HsmState.S4_SHIELD_ARMED_24H:
            out = transition(
                state,
                HsmEvent.T2_SHIELD_24H_COMPLETED,
                now_ms=now_ms,
                entered_at_ms=f.entered_at_ms,
                shield_expires_at_ms=f.shield_expires_at_ms,
            )
            if out.accepted:
                return dataclasses.replace(f, state=out.to_state, updated_at_ms=now_ms), out
            return f, resumed
        if state in _POST_INTERCEPT_STATES and now_ms >= FiberSupervisor._shield_horizon_ms(f):
            out = TransitionOutcome(
                state, HsmEvent.T2_SHIELD_24H_COMPLETED, HsmState.S0_IDLE, accepted=True
            )
            return dataclasses.replace(f, state=HsmState.S0_IDLE, updated_at_ms=now_ms), out
        return f, None

    @staticmethod
    def _resume_if_needed(f: LoanJourneyFiber, event: HsmEvent, now_ms: int) -> LoanJourneyFiber:
        """S8 → S4 ("Resume remaining watch") khi S8 nhận sự kiện không có trong bảng (vd E6 mở
        lại app ngân hàng, tiền về thêm) và Khiên còn hạn — không để luồng kẹt ở S8."""
        if f.state is not HsmState.S8_SAFE_ROUTINE_TX:
            return f
        if (f.state, event) in TRANSITIONS or event in GLOBAL_TRANSITIONS:
            return f
        horizon = FiberSupervisor._shield_horizon_ms(f)
        if now_ms >= horizon:
            return f
        out = transition(
            f.state,
            HsmEvent.A_RESUME_REMAINING_WATCH,
            now_ms=now_ms,
            entered_at_ms=f.entered_at_ms,
            shield_expires_at_ms=f.shield_expires_at_ms,
        )
        return dataclasses.replace(
            f,
            state=out.to_state,
            entered_at_ms=now_ms,
            updated_at_ms=max(f.updated_at_ms, now_ms),
            shield_expires_at_ms=horizon,
        )

    def apply_event(
        self,
        fiber_key: str,
        event: HsmEvent,
        *,
        now_ms: int,
        event_id: str,
        source: EvidenceSource | None = None,
        shield_ms: int | None = None,
    ) -> TransitionOutcome:
        fresh = LoanJourneyFiber(
            fiber_key=fiber_key,
            session_id=self._new_session_id() if fiber_key not in self._fibers else "",
            state=HsmState.S0_IDLE,
            entered_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        fiber = self._fibers.get(fiber_key)
        if fiber is not None:
            fiber, _ = self._expire_one(fiber, now_ms)
            if fiber.state is HsmState.S0_IDLE:  # hết hạn → bắt đầu hành trình mới, cùng phiên
                fiber = dataclasses.replace(fresh, session_id=fiber.session_id)
            fiber = self._resume_if_needed(fiber, event, now_ms)
        else:
            fiber = fresh

        out = self._credit_outcome(fiber, event, now_ms)
        evidence = fiber.evidence
        kind = EVENT_EVIDENCE.get(event)
        if kind is not None and out.to_state is not HsmState.S0_IDLE:
            evidence = _merge_evidence(evidence, [Evidence(kind, now_ms, event_id)])
        expires = fiber.shield_expires_at_ms
        if event in DISBURSEMENT_EVENTS:
            if out.to_state in _EXTEND_ONLY_STATES:
                duration = shield_ms if shield_ms is not None else SHIELD_ARM_MS[event]
                expires = _max_opt(expires, now_ms + duration)
        elif out.accepted and event in SHIELD_ARM_MS:
            duration = shield_ms if shield_ms is not None else SHIELD_ARM_MS[event]
            expires = _max_opt(expires, now_ms + duration)
        if out.accepted and out.to_state is HsmState.S4_SHIELD_ARMED_24H and expires is None:
            expires = now_ms + SHIELD_DEFAULT_MS
        sources = fiber.evidence_sources | ({source} if source is not None else set())
        self._store(
            LoanJourneyFiber(
                fiber_key=fiber.fiber_key,
                session_id=fiber.session_id,
                state=out.to_state,
                entered_at_ms=now_ms if out.changed else fiber.entered_at_ms,
                updated_at_ms=max(fiber.updated_at_ms, now_ms),
                shield_expires_at_ms=expires,
                evidence=evidence,
                evidence_sources=frozenset(sources),
            )
        )
        return out

    @staticmethod
    def _credit_outcome(f: LoanJourneyFiber, event: HsmEvent, now_ms: int) -> TransitionOutcome:
        """Ánh xạ tiền về theo trạng thái hiện tại (xem docstring module); còn lại tra bảng."""
        if event in DISBURSEMENT_EVENTS:
            if f.state is HsmState.S1_LOAN_EXPLORATION:
                event = HsmEvent.E3_ANY_ORG_CREDIT_INFLOW
            elif f.state is HsmState.S2_CONTRACT_AUDITING:
                return TransitionOutcome(
                    f.state, event, HsmState.S4_SHIELD_ARMED_24H, accepted=True
                )
        return transition(
            f.state,
            event,
            now_ms=now_ms,
            entered_at_ms=f.entered_at_ms,
            shield_expires_at_ms=f.shield_expires_at_ms,
        )

    @staticmethod
    def _same_disbursement(f: LoanJourneyFiber, source: EvidenceSource, now_ms: int) -> bool:
        """S2-EC43: luồng đã có khoản giải ngân ≤ 30 phút từ NGUỒN KHÁC → cùng một khoản tiền."""
        d = f.latest_disbursement_ms
        return (
            d is not None
            and abs(now_ms - d) <= RACE_MERGE_WINDOW_MS
            and source not in f.evidence_sources
        )

    def _race_target(self, fiber_key: str, source: EvidenceSource, now_ms: int) -> tuple[str, bool]:
        """S2-EC43: thông báo trên máy + webhook B2B cho cùng một khoản (≤ 30 phút, khác nguồn,
        một bên dùng khóa mặc định) → cùng một luồng; khóa cụ thể (không phải default) thắng.
        Trả về (khóa luồng đích, có phải cùng một khoản đã ghi nhận hay không)."""
        cur = self._fibers.get(fiber_key)
        if cur is not None:
            return fiber_key, self._same_disbursement(cur, source, now_ms)
        candidates = sorted(self._fibers.values(), key=lambda f: -f.updated_at_ms)
        for f in candidates:
            if self._same_disbursement(f, source, now_ms) and DEFAULT_FIBER_KEY in (
                fiber_key,
                f.fiber_key,
            ):
                if f.fiber_key == DEFAULT_FIBER_KEY and fiber_key != DEFAULT_FIBER_KEY:
                    del self._fibers[f.fiber_key]
                    self._fibers[fiber_key] = dataclasses.replace(f, fiber_key=fiber_key)
                    return fiber_key, True
                return f.fiber_key, True
        return fiber_key, False

    def arm_disbursement(
        self,
        fiber_key: str,
        event: HsmEvent,
        *,
        now_ms: int,
        event_id: str,
        source: EvidenceSource,
        shield_ms: int | None = None,
    ) -> LoanJourneyFiber | None:
        """Tiền về (E3/E4/E5): kích hoạt/gia hạn Khiên theo MAX, gộp nguồn bằng chứng.

        S2-EC43: cùng một khoản báo từ 2 nguồn (B2B + thông báo trên máy) → chỉ gộp nguồn + MAX
        hạn Khiên, KHÔNG thêm bằng chứng DISBURSEMENT thứ hai (tránh cộng 2 lần w_disb).

        Trả về ``None`` khi bị từ chối rõ ràng: E3 (tín dụng độ tin cậy thấp) lúc chưa có hành
        trình vay (S0) — không tạo luồng/Khiên. Giả định: Khiên V2 pre-arm 72h KHÔNG bị rút ngắn
        về 24h khi tiền về (lấy MAX, lệch spec "72h -> 24h on Credit" theo hướng an toàn)."""
        if event not in DISBURSEMENT_EVENTS:
            raise ValueError("arm_disbursement chỉ nhận E3/E4/E5")
        target, same = self._race_target(fiber_key, source, now_ms)
        if same:
            f = self._fibers[target]
            duration = shield_ms if shield_ms is not None else SHIELD_ARM_MS[event]
            merged = dataclasses.replace(
                f,
                updated_at_ms=max(f.updated_at_ms, now_ms),
                shield_expires_at_ms=_max_opt(f.shield_expires_at_ms, now_ms + duration),
                evidence_sources=f.evidence_sources | {source},
            )
            self._fibers[target] = merged
            return merged
        self.apply_event(
            target, event, now_ms=now_ms, event_id=event_id, source=source, shield_ms=shield_ms
        )
        return self._fibers.get(target)

    def broadcast(self, event: HsmEvent, *, now_ms: int) -> list[TransitionOutcome]:
        """Sự kiện cấp thiết bị (vd E6 mở app ngân hàng): áp lên mọi luồng nhận được nó.
        Trước đó dọn luồng hết hạn và đưa S8 còn Khiên về S4 (không bị UNDEFINED)."""
        self.expire(now_ms)
        outs: list[TransitionOutcome] = []
        for key in sorted(self._fibers):
            f = self._resume_if_needed(self._fibers[key], event, now_ms)
            out = transition(
                f.state,
                event,
                now_ms=now_ms,
                entered_at_ms=f.entered_at_ms,
                shield_expires_at_ms=f.shield_expires_at_ms,
            )
            outs.append(out)
            if out.accepted:
                f = dataclasses.replace(
                    f,
                    state=out.to_state,
                    entered_at_ms=now_ms if out.changed else f.entered_at_ms,
                    updated_at_ms=max(f.updated_at_ms, now_ms),
                )
            self._store(f)
        return outs

    def expire(self, now_ms: int) -> list[TransitionOutcome]:
        """Dọn/hết hạn mọi luồng (quy tắc ở docstring module); luồng về S0 bị loại khỏi tập."""
        outs: list[TransitionOutcome] = []
        for key in sorted(self._fibers):
            f, out = self._expire_one(self._fibers[key], now_ms)
            if out is not None:
                outs.append(out)
                self._store(f)
        return outs
