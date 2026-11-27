"""Webhook B2B kích hoạt Khiên (File 4 §4.2(B), S2-EC26, S2-EC43, EC-DB03, EC-DB19).

Luồng (sau khi route đã xác thực chữ ký + timestamp):

1. ``claim_b2b`` (Redis, TTL 48h): chỉ yêu cầu đầu tiên đi tiếp; trùng khóa → trả lại kết quả cũ
   nếu nội dung giống, ``IDEMPOTENCY_CONFLICT`` nếu khác (S2-EC26). Redis lỗi → ``degraded``, coi
   là lần đầu; ràng buộc ``UNIQUE (partner_id, idempotency_key)`` của repo là chốt chặn cuối.
2. Khớp thiết bị theo ``phone_blind_hmac``. Opt-in hết hạn (``phone_optin_exp_at`` ≤ now, EC-DB03
   số điện thoại bị thu hồi cấp lại) → KHÔNG bật Khiên: ``NO_DEVICE_OPTED_IN``.
3. Bật Khiên qua ``ShieldStateStore.arm_shield`` (E5_B2B_WEBHOOK, nguồn ``B2B_CONFIRMED``): hợp
   nhất nguyên tử với thông báo trên máy (S2-EC43, MAX hạn Khiên, không tạo 2 phiên). Store lỗi /
   từ chối → ``PENDING_USER_ONLINE`` (đã ghi nhận, thiết bị đồng bộ khi online); không im lặng.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from app.core.errors import AppError, ErrorCode
from app.core.ids import new_uuid7
from app.core.logging import get_logger
from app.core.schemas.b2b import (
    B2BShieldStatus,
    DisbursementShieldRequest,
    DisbursementShieldResponse,
    PreSimoWaveWarning,
)
from app.mule_graph.models import DeviceMatch, PartnerRecord, ShieldRecord
from app.mule_graph.repository import B2BRepository
from app.shield_state.fibers import DEFAULT_FIBER_KEY, EvidenceSource
from app.shield_state.hsm import HsmEvent
from app.shield_state.idempotency import claim_b2b
from app.shield_state.keys import b2b_idem_key
from app.shield_state.store import ShieldStateStore

WAVE_LOOKBACK: Final = timedelta(hours=24)
_log = get_logger("vayshield.b2b.shield")


def _fingerprint(s: ShieldRecord) -> tuple[str, str, int]:
    return (s.borrower_phone_hmac, s.amount_bucket_tier, s.shield_duration_hours)


class B2BShieldService:
    def __init__(self, repo: B2BRepository, store: ShieldStateStore, redis: Redis) -> None:
        self._repo = repo
        self._store = store
        self._redis = redis

    async def disbursement_shield(
        self,
        partner: PartnerRecord,
        body: DisbursementShieldRequest,
        idempotency_key: str,
        now: datetime,
    ) -> DisbursementShieldResponse:
        shield_id = new_uuid7()
        hours = body.shield_duration_hours
        candidate = ShieldRecord(
            id=shield_id,
            partner_id=partner.id,
            idempotency_key=idempotency_key,
            borrower_phone_hmac=body.borrower_phone_blind_hmac,
            amount_bucket_tier=body.amount_bucket_tier.value,
            shield_duration_hours=hours,
            status=B2BShieldStatus.NO_DEVICE_OPTED_IN,
            shield_expires_at=now + timedelta(hours=hours),
            created_at=now,
        )
        claim = await claim_b2b(self._redis, str(partner.id), idempotency_key, str(shield_id))
        if not claim.first:
            return await self._replay(partner, candidate, now)
        try:
            # Chốt trùng ở DB TRƯỚC khi bật Khiên: khóa Redis có thể đã hết hạn/mất (degraded),
            # nhưng ``UNIQUE (partner_id, idempotency_key)`` là nguồn sự thật (S2-EC26).
            if await self._repo.get_shield(partner.id, idempotency_key) is not None:
                return await self._replay(partner, candidate, now)
            status, device_id = await self._arm(candidate, now)
            record, created = await self._repo.create_shield(
                _with(candidate, status=status, matched_device_id=device_id)
            )
        except BaseException:
            await self._release(partner.id, idempotency_key)  # retry không bị kẹt 48h
            raise
        if not created:  # đua với yêu cầu song song cùng khóa: so với NỘI DUNG của yêu cầu này
            return await self._replay(partner, candidate, now, existing=record)
        return await self._response(partner, record, now)

    async def _replay(
        self,
        partner: PartnerRecord,
        candidate: ShieldRecord,
        now: datetime,
        *,
        existing: ShieldRecord | None = None,
    ) -> DisbursementShieldResponse:
        stored = existing or await self._repo.get_shield(partner.id, candidate.idempotency_key)
        if stored is None:  # bản gốc còn đang xử lý
            raise AppError(ErrorCode.CONFLICT, 409, "Yêu cầu trùng khóa đang được xử lý.")
        if _fingerprint(stored) != _fingerprint(candidate):
            raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, 409)
        return await self._response(partner, stored, now)

    async def _arm(
        self, shield: ShieldRecord, now: datetime
    ) -> tuple[B2BShieldStatus, UUID | None]:
        match: DeviceMatch | None = await self._repo.find_device_by_phone_hmacs(
            (shield.borrower_phone_hmac,)
        )
        if match is None:
            return B2BShieldStatus.NO_DEVICE_OPTED_IN, None
        if match.phone_optin_exp_at is None or match.phone_optin_exp_at <= now:
            # EC-DB03: opt-in quá 90 ngày chưa gia hạn → có thể là số đã cấp lại cho người khác.
            return B2BShieldStatus.NO_DEVICE_OPTED_IN, None
        try:
            _, fiber = await self._store.arm_shield(
                str(match.device_id),
                DEFAULT_FIBER_KEY,
                HsmEvent.E5_B2B_WEBHOOK,
                now_ms=int(now.timestamp() * 1000),
                event_id=str(shield.id),
                source=EvidenceSource.B2B_CONFIRMED,
                shield_ms=shield.shield_duration_hours * 3_600_000,
            )
        except AppError:
            _log.warning("b2b_shield_arm_degraded")
            return B2BShieldStatus.PENDING_USER_ONLINE, match.device_id
        if fiber is None:
            return B2BShieldStatus.PENDING_USER_ONLINE, match.device_id
        return B2BShieldStatus.ARMED_ON_DEVICE, match.device_id

    async def _response(
        self, partner: PartnerRecord, record: ShieldRecord, now: datetime
    ) -> DisbursementShieldResponse:
        waves = await self._repo.count_active_waves(partner.lender_registry_id, now - WAVE_LOOKBACK)
        return DisbursementShieldResponse(
            shield_id=record.id,
            status=record.status,
            shield_expires_at=record.shield_expires_at,
            pre_simo_active_wave_warning=PreSimoWaveWarning(
                impersonation_wave_active=waves > 0, active_clusters_targeting_brand=waves
            ),
        )

    async def _release(self, partner_id: UUID, idempotency_key: str) -> None:
        try:
            await self._redis.delete(b2b_idem_key(str(partner_id), idempotency_key))
        except Exception:  # best-effort
            _log.warning("b2b_idem_release_failed")


def _with(
    s: ShieldRecord, *, status: B2BShieldStatus, matched_device_id: UUID | None
) -> ShieldRecord:
    return ShieldRecord(
        id=s.id,
        partner_id=s.partner_id,
        idempotency_key=s.idempotency_key,
        borrower_phone_hmac=s.borrower_phone_hmac,
        amount_bucket_tier=s.amount_bucket_tier,
        shield_duration_hours=s.shield_duration_hours,
        status=status,
        shield_expires_at=s.shield_expires_at,
        created_at=s.created_at,
        matched_device_id=matched_device_id,
    )
