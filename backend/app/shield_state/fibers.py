"""Bộ giám sát đa luồng vay ``FiberSupervisor`` (K = 8) + toán tử join-semilattice (File 2 §2.1).

Thuần (không I/O, không đọc đồng hồ). Lớp Redis (``store.py``) đọc bản ghi → dựng supervisor →
áp thao tác → ghi lại bằng CAS (EC-DB07).
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
    SHIELD_ARM_MS,
    SHIELD_DEFAULT_MS,
    STATE_SEVERITY,
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
        fiber = self._fibers.get(fiber_key) or LoanJourneyFiber(
            fiber_key=fiber_key,
            session_id=self._new_session_id(),
            state=HsmState.S0_IDLE,
            entered_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        out = transition(
            fiber.state,
            event,
            now_ms=now_ms,
            entered_at_ms=fiber.entered_at_ms,
            shield_expires_at_ms=fiber.shield_expires_at_ms,
        )
        evidence = fiber.evidence
        kind = EVENT_EVIDENCE.get(event)
        if kind is not None:
            evidence = _merge_evidence(evidence, [Evidence(kind, now_ms, event_id)])
        expires = fiber.shield_expires_at_ms
        if event in DISBURSEMENT_EVENTS or (out.accepted and event in SHIELD_ARM_MS):
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

    def _race_target(self, fiber_key: str, source: EvidenceSource, now_ms: int) -> str:
        """S2-EC43: thông báo trên máy + webhook B2B cho cùng một khoản (≤ 30 phút, khác nguồn,
        một bên dùng khóa mặc định) → cùng một luồng; khóa cụ thể (không phải default) thắng."""
        if fiber_key in self._fibers:
            return fiber_key
        candidates = sorted(self._fibers.values(), key=lambda f: -f.updated_at_ms)
        for f in candidates:
            d = f.latest_disbursement_ms
            if (
                d is not None
                and abs(now_ms - d) <= RACE_MERGE_WINDOW_MS
                and source not in f.evidence_sources
                and DEFAULT_FIBER_KEY in (fiber_key, f.fiber_key)
            ):
                if f.fiber_key == DEFAULT_FIBER_KEY and fiber_key != DEFAULT_FIBER_KEY:
                    del self._fibers[f.fiber_key]
                    self._fibers[fiber_key] = dataclasses.replace(f, fiber_key=fiber_key)
                    return fiber_key
                return f.fiber_key
        return fiber_key

    def arm_disbursement(
        self,
        fiber_key: str,
        event: HsmEvent,
        *,
        now_ms: int,
        event_id: str,
        source: EvidenceSource,
        shield_ms: int | None = None,
    ) -> LoanJourneyFiber:
        """Tiền về (E3/E4/E5): kích hoạt/gia hạn Khiên theo MAX, gộp nguồn bằng chứng."""
        if event not in DISBURSEMENT_EVENTS:
            raise ValueError("arm_disbursement chỉ nhận E3/E4/E5")
        target = self._race_target(fiber_key, source, now_ms)
        self.apply_event(
            target, event, now_ms=now_ms, event_id=event_id, source=source, shield_ms=shield_ms
        )
        fiber = self._fibers.get(target)
        if fiber is None:  # pragma: no cover - E4/E5 luôn dẫn tới trạng thái ≠ S0
            raise RuntimeError("luồng giải ngân biến mất")
        return fiber

    def broadcast(self, event: HsmEvent, *, now_ms: int) -> list[TransitionOutcome]:
        """Sự kiện cấp thiết bị (vd E6 mở app ngân hàng): áp lên mọi luồng nhận được nó."""
        outs: list[TransitionOutcome] = []
        for key in sorted(self._fibers):
            f = self._fibers[key]
            out = transition(
                f.state,
                event,
                now_ms=now_ms,
                entered_at_ms=f.entered_at_ms,
                shield_expires_at_ms=f.shield_expires_at_ms,
            )
            outs.append(out)
            if out.accepted:
                self._store(
                    dataclasses.replace(
                        f,
                        state=out.to_state,
                        entered_at_ms=now_ms if out.changed else f.entered_at_ms,
                        updated_at_ms=max(f.updated_at_ms, now_ms),
                    )
                )
        return outs

    def expire(self, now_ms: int) -> list[TransitionOutcome]:
        """T1 (S1 quá 72h) và T2 (S4 hết hạn Khiên) → S0 → loại khỏi tập."""
        outs: list[TransitionOutcome] = []
        for key in sorted(self._fibers):
            f = self._fibers[key]
            if f.state is HsmState.S1_LOAN_EXPLORATION:
                ev = HsmEvent.T1_MONOTONIC_TTL_EXPIRED
            elif f.state is HsmState.S4_SHIELD_ARMED_24H:
                ev = HsmEvent.T2_SHIELD_24H_COMPLETED
            else:
                continue
            out = transition(
                f.state,
                ev,
                now_ms=now_ms,
                entered_at_ms=f.entered_at_ms,
                shield_expires_at_ms=f.shield_expires_at_ms,
            )
            if out.accepted:
                outs.append(out)
                self._store(dataclasses.replace(f, state=out.to_state, updated_at_ms=now_ms))
        return outs
