"""Lưu/đọc phiên bảo vệ ở PostgreSQL ``protection_sessions`` cho Trạm 3 (File 4 §2.2, EC-DB11).

Redis là nguồn sự thật nóng; PostgreSQL là bản dự phòng khi Redis mất khóa/chậm
(``ShieldStateStore`` nhận ``FallbackReader``). Hai chiều đều "best-effort có chủ đích":

- ``save`` KHÔNG BAO GIỜ làm hỏng request (Redis đã ghi xong); lỗi chỉ log cảnh báo.
- ``read_fibers`` trả ``None`` khi PG xác nhận không có dữ liệu, và NÉM lỗi khi PG hỏng để store
  rơi về trạng thái an toàn (``degraded``) thay vì coi như "không có Khiên".

Giả định/mất mát có chủ đích: bảng không lưu danh sách bằng chứng; khi dựng lại luồng từ PG ta chỉ
tạo 1 bằng chứng ``DISBURSEMENT`` tại ``disbursement_at`` (nếu có) — đủ để giữ Mức Cam/Đỏ.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final, Protocol
from uuid import UUID

from sqlalchemy import text

from app.core.logging import get_logger
from app.core.schemas.common import AmountBucketTier, HsmState, TriggerOrigin
from app.shield_state.fibers import SOURCE_FROM_ORIGIN, EvidenceSource, LoanJourneyFiber
from app.shield_state.risk import Evidence, EvidenceKind

logger = get_logger("vayshield.realtime.persistence")

_ORIGIN_BY_SOURCE: Final[dict[EvidenceSource, TriggerOrigin]] = {
    v: k for k, v in SOURCE_FROM_ORIGIN.items()
}
LOOKBACK_HOURS: Final = 96  # > 72h (Khiên tối đa) để không bỏ sót phiên sắp hết hạn


class ShieldPersistence(Protocol):
    async def save(
        self,
        device_id: UUID,
        fibers: Sequence[LoanJourneyFiber],
        *,
        sandbox_namespace: str,
        trigger_origin: TriggerOrigin | None,
        amount_bucket_tier: AmountBucketTier | None,
        disbursement_at: datetime | None,
        risk_log_odds: float,
    ) -> None: ...


def _ms(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp() * 1000)


def _dt(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=UTC)


class NullShieldPersistence:
    async def save(self, *_: Any, **__: Any) -> None:
        return None


class PgShieldPersistence:
    async def save(
        self,
        device_id: UUID,
        fibers: Sequence[LoanJourneyFiber],
        *,
        sandbox_namespace: str,
        trigger_origin: TriggerOrigin | None,
        amount_bucket_tier: AmountBucketTier | None,
        disbursement_at: datetime | None,
        risk_log_odds: float,
    ) -> None:
        from app.core.db import short_session

        try:
            async with short_session() as session, session.begin():
                for f in fibers:
                    try:
                        sid = UUID(f.session_id)
                    except ValueError:
                        continue
                    origin = trigger_origin or next(
                        (
                            _ORIGIN_BY_SOURCE[s]
                            for s in sorted(f.evidence_sources, key=lambda x: x.value)
                            if s in _ORIGIN_BY_SOURCE
                        ),
                        TriggerOrigin.STATION_3_NOTIF,
                    )
                    await session.execute(
                        text(
                            "INSERT INTO protection_sessions (id, device_id, fiber_key,"
                            " sandbox_namespace, current_hsm_state, risk_log_odds, trigger_origin,"
                            " amount_bucket_tier, disbursement_at, shield_expires_at)"
                            " VALUES (:id, :dev, :fk, :ns, :st, :lo, :origin, :tier, :disb, :exp)"
                            " ON CONFLICT (id) DO UPDATE SET"
                            " current_hsm_state = EXCLUDED.current_hsm_state,"
                            " risk_log_odds = EXCLUDED.risk_log_odds,"
                            " shield_expires_at = EXCLUDED.shield_expires_at, updated_at = NOW()"
                        ),
                        {
                            "id": sid,
                            "dev": device_id,
                            "fk": f.fiber_key,
                            "ns": sandbox_namespace,
                            "st": f.state.value,
                            "lo": risk_log_odds,
                            "origin": origin.value,
                            "tier": None
                            if amount_bucket_tier is None
                            else amount_bucket_tier.value,
                            "disb": disbursement_at or _dt(f.latest_disbursement_ms),
                            "exp": _dt(f.shield_expires_at_ms),
                        },
                    )
        except Exception:
            logger.warning("shield_persist_failed: không lưu được protection_sessions")


class PgFallbackReader:
    """``FallbackReader`` của ``ShieldStateStore``: dựng lại luồng từ ``protection_sessions``."""

    def __init__(self, sandbox_namespace: str = "live") -> None:
        self._namespace = sandbox_namespace

    async def __call__(self, device_id: str) -> Sequence[LoanJourneyFiber] | None:
        from app.core.db import short_session

        async with short_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, fiber_key, current_hsm_state, trigger_origin, disbursement_at,"
                        " shield_expires_at, created_at, updated_at FROM protection_sessions"
                        " WHERE device_id = :dev AND sandbox_namespace = :ns"
                        " AND updated_at > NOW() - make_interval(hours => :h)"
                        " ORDER BY updated_at DESC LIMIT 8"
                    ),
                    {"dev": UUID(device_id), "ns": self._namespace, "h": LOOKBACK_HOURS},
                )
            ).all()
        if not rows:
            return None
        fibers: list[LoanJourneyFiber] = []
        for r in rows:
            disb_ms = _ms(r.disbursement_at)
            evidence = (
                (Evidence(EvidenceKind.DISBURSEMENT, disb_ms, f"pg:{r.id}"),)
                if disb_ms is not None
                else ()
            )
            try:
                source = SOURCE_FROM_ORIGIN[TriggerOrigin(r.trigger_origin)]
            except (KeyError, ValueError):
                source = EvidenceSource.EDGE_NOTIF
            fibers.append(
                LoanJourneyFiber(
                    fiber_key=r.fiber_key,
                    session_id=str(r.id),
                    state=HsmState(r.current_hsm_state),
                    entered_at_ms=_ms(r.created_at) or 0,
                    updated_at_ms=_ms(r.updated_at) or 0,
                    shield_expires_at_ms=_ms(r.shield_expires_at),
                    evidence=evidence,
                    evidence_sources=frozenset({source}),
                )
            )
        return fibers
