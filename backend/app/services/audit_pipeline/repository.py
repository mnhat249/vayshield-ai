"""Lưu trữ thiết bị & audit (File 4 §2.2: ``anonymous_devices``, ``contract_audits``).

EC-DB01: mọi hàm SQL mượn ``short_session()`` RIÊNG cho từng thao tác và trả ngay — luồng SSE
không bao giờ giữ kết nối PostgreSQL trong lúc chờ Gemini. Dữ liệu ghi vào
``masked_extraction_json`` đã qua ``validate_masked_extraction`` (EC-DB18, EC-DB26) và đã che
PII (``LoanFacts.to_json``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Final, Protocol
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text

from app.core.schemas.common import TriggerOrigin
from app.db_schema.sanitize import validate_masked_extraction

PROTECTION_STATE_AUDITING: Final = "S2_CONTRACT_AUDITING"
SESSION_REUSE_WINDOW_H: Final = 4  # trùng cửa sổ S2 "không tiến triển sau 4h" (fibers.py)


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    device_id: UUID
    pubkey_spki: bytes
    platform_type: str
    daily_wage_vnd: int
    policy_version: int
    phone_blind_hmac: str | None = None
    pepper_version: int = 1
    phone_optin_exp_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: UUID
    device_id: UUID
    sandbox_namespace: str
    fiber_key: str
    trigger_origin: TriggerOrigin
    image_count: int
    verdict_code: str
    lender_category_resolved: str
    claimed_brand_normalized: str | None
    matched_lender_id: UUID | None
    nominal_principal_vnd: int | None
    net_disbursed_vnd: int | None
    total_repayment_vnd: int | None
    tenure_days: int | None
    apr_vn_365: float | None
    apr_min_bound: float | None
    apr_max_bound: float | None
    ear_xirr: float | None
    lost_workdays: float | None
    upfront_fee_detected: bool
    brand_spoof_detected: bool
    vietqr_extracted: bool
    user_edited_numbers: bool
    context: dict[str, Any]
    cited_clauses: tuple[str, ...]
    latency_ms: int
    model_tier: str
    created_at: datetime | None = None


class DeviceRepository(Protocol):
    async def create(self, device: DeviceRecord) -> bool:
        """Tạo thiết bị. Trả ``True`` nếu SĐT opt-in được liên kết; ``False`` nếu SĐT đã thuộc
        thiết bị khác (không chiếm — chưa xác minh quyền sở hữu SĐT, EC-DB03)."""
        ...

    async def get(self, device_id: UUID) -> DeviceRecord | None: ...

    async def erase(self, device_id: UUID, redis: Redis | None) -> tuple[int, int]:
        """Xóa toàn bộ dữ liệu thiết bị (Luật 91/2025, EC-DB21). Trả
        ``(liên kết người thân bị hủy, cạnh mule giữ lại dạng bia mộ ẩn danh)``. Idempotent."""
        ...


class AuditRepository(Protocol):
    async def save(self, audit: AuditRecord) -> None: ...

    async def get(
        self, audit_id: UUID, device_id: UUID, sandbox_namespace: str
    ) -> AuditRecord | None:
        """Chỉ trả audit của CHÍNH thiết bị + namespace đó (không lộ audit người khác)."""
        ...

    async def update_recalculated(self, audit: AuditRecord) -> None: ...


# ------------------------------------------------------------------ in-memory ---


@dataclass
class InMemoryDeviceRepository:
    devices: dict[UUID, DeviceRecord] = field(default_factory=dict)

    async def create(self, device: DeviceRecord) -> bool:
        linked = device.phone_blind_hmac is not None and not any(
            d.phone_blind_hmac == device.phone_blind_hmac for d in self.devices.values()
        )
        if not linked:
            device = replace(device, phone_blind_hmac=None, phone_optin_exp_at=None)
        self.devices[device.device_id] = device
        return linked

    async def get(self, device_id: UUID) -> DeviceRecord | None:
        return self.devices.get(device_id)

    async def erase(self, device_id: UUID, redis: Redis | None) -> tuple[int, int]:
        self.devices.pop(device_id, None)
        return 0, 0


@dataclass
class InMemoryAuditRepository:
    audits: dict[UUID, AuditRecord] = field(default_factory=dict)
    open_sessions: int = 0  # test EC-DB01: không có thao tác DB dang dở lúc chờ LLM

    async def save(self, audit: AuditRecord) -> None:
        validate_masked_extraction(audit.context)
        self.audits[audit.audit_id] = audit

    async def get(
        self, audit_id: UUID, device_id: UUID, sandbox_namespace: str
    ) -> AuditRecord | None:
        found = self.audits.get(audit_id)
        if found is None or found.device_id != device_id:
            return None
        if found.sandbox_namespace != sandbox_namespace:
            return None
        return found

    async def update_recalculated(self, audit: AuditRecord) -> None:
        validate_masked_extraction(audit.context)
        self.audits[audit.audit_id] = audit


# ------------------------------------------------------------------ PostgreSQL ---


class SqlDeviceRepository:
    async def erase(self, device_id: UUID, redis: Redis | None) -> tuple[int, int]:
        from app.core.db import init_engine, short_session
        from app.core.errors import AppError, ErrorCode
        from app.security.erasure import erase_device_and_commit

        engine = init_engine()
        if engine is None:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
        async with short_session() as session:
            links = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM family_guardian_links"
                        " WHERE borrower_device_id = :id AND status IN"
                        " ('PENDING_CONFIRM', 'ACTIVE', 'PUSH_EXPIRED_410')"
                    ),
                    {"id": device_id},
                )
            ).scalar_one()
        receipt = await erase_device_and_commit(engine, redis, device_id)
        return int(links), receipt.contributions_preserved

    async def create(self, device: DeviceRecord) -> bool:
        from app.core.db import short_session

        params = {
            "id": device.device_id,
            "pk": device.pubkey_spki,
            "plat": device.platform_type,
            "wage": device.daily_wage_vnd,
            "pv": device.policy_version,
            "pepper": device.pepper_version,
        }
        insert = text(
            "INSERT INTO anonymous_devices (id, pubkey_ecdsa_p256, platform_type, daily_wage_vnd,"
            " policy_version, phone_blind_hmac, pepper_version, phone_optin_exp_at)"
            " VALUES (:id, :pk, :plat, :wage, :pv, :phone, :pepper, :exp)"
        )
        async with short_session() as session, session.begin():
            if device.phone_blind_hmac is not None:
                taken = (
                    await session.execute(
                        text("SELECT 1 FROM anonymous_devices WHERE phone_blind_hmac = :h"),
                        {"h": device.phone_blind_hmac},
                    )
                ).first()
                if taken is None:
                    await session.execute(
                        insert,
                        {
                            **params,
                            "phone": device.phone_blind_hmac,
                            "exp": device.phone_optin_exp_at,
                        },
                    )
                    return True
            await session.execute(insert, {**params, "phone": None, "exp": None})
            return False

    async def get(self, device_id: UUID) -> DeviceRecord | None:
        from app.core.db import short_session

        async with short_session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, pubkey_ecdsa_p256, platform_type, daily_wage_vnd,"
                        " policy_version, phone_blind_hmac, pepper_version, phone_optin_exp_at"
                        " FROM anonymous_devices WHERE id = :id"
                    ),
                    {"id": device_id},
                )
            ).first()
        if row is None:
            return None
        return DeviceRecord(
            device_id=row.id,
            pubkey_spki=bytes(row.pubkey_ecdsa_p256 or b""),
            platform_type=row.platform_type,
            daily_wage_vnd=int(row.daily_wage_vnd),
            policy_version=int(row.policy_version),
            phone_blind_hmac=row.phone_blind_hmac,
            pepper_version=int(row.pepper_version),
            phone_optin_exp_at=row.phone_optin_exp_at,
        )


_INSERT_AUDIT: Final = text(
    "INSERT INTO contract_audits (id, session_id, image_count, verdict_code,"
    " lender_category_resolved, claimed_brand_normalized, matched_lender_id,"
    " nominal_principal_vnd, net_disbursed_vnd, total_repayment_vnd, tenure_days, apr_vn_365,"
    " apr_min_bound, apr_max_bound, ear_xirr, lost_workdays, upfront_fee_detected,"
    " brand_spoof_detected, vietqr_extracted, user_edited_numbers, masked_extraction_json,"
    " cited_legal_clauses, processing_latency_ms, model_tier_used)"
    " VALUES (:id, :sid, :imgs, :verdict, :cat, :brand, :lender, :p, :n, :t, :d, :apr, :lo, :hi,"
    " :ear, :lost, :upfront, :spoof, :qr, :edited, CAST(:ctx AS JSONB), :cited, :lat, :tier)"
)
_UPDATE_AUDIT: Final = text(
    "UPDATE contract_audits SET verdict_code = :verdict, lender_category_resolved = :cat,"
    " matched_lender_id = :lender, nominal_principal_vnd = :p, net_disbursed_vnd = :n,"
    " total_repayment_vnd = :t, tenure_days = :d, apr_vn_365 = :apr, apr_min_bound = :lo,"
    " apr_max_bound = :hi, ear_xirr = :ear, lost_workdays = :lost, upfront_fee_detected = :upfront,"
    " brand_spoof_detected = :spoof, user_edited_numbers = TRUE,"
    " masked_extraction_json = CAST(:ctx AS JSONB), cited_legal_clauses = :cited"
    " WHERE id = :id"
)


def _params(audit: AuditRecord) -> dict[str, Any]:
    return {
        "id": audit.audit_id,
        "imgs": audit.image_count,
        "verdict": audit.verdict_code,
        "cat": audit.lender_category_resolved,
        "brand": audit.claimed_brand_normalized,
        "lender": audit.matched_lender_id,
        "p": audit.nominal_principal_vnd,
        "n": audit.net_disbursed_vnd,
        "t": audit.total_repayment_vnd,
        "d": audit.tenure_days,
        "apr": audit.apr_vn_365,
        "lo": audit.apr_min_bound,
        "hi": audit.apr_max_bound,
        "ear": audit.ear_xirr,
        "lost": audit.lost_workdays,
        "upfront": audit.upfront_fee_detected,
        "spoof": audit.brand_spoof_detected,
        "qr": audit.vietqr_extracted,
        "edited": audit.user_edited_numbers,
        "ctx": json.dumps(validate_masked_extraction(audit.context), ensure_ascii=False),
        "cited": list(audit.cited_clauses),
        "lat": audit.latency_ms,
        "tier": audit.model_tier,
    }


class SqlAuditRepository:
    async def save(self, audit: AuditRecord) -> None:
        from app.core.db import short_session

        params = _params(audit)  # validate TRƯỚC khi mượn kết nối
        async with short_session() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT id FROM protection_sessions WHERE device_id = :dev"
                        " AND fiber_key = :fk AND sandbox_namespace = :ns"
                        " AND updated_at > NOW() - make_interval(hours => :h)"
                        " ORDER BY updated_at DESC LIMIT 1"
                    ),
                    {
                        "dev": audit.device_id,
                        "fk": audit.fiber_key,
                        "ns": audit.sandbox_namespace,
                        "h": SESSION_REUSE_WINDOW_H,
                    },
                )
            ).first()
            if row is not None:
                session_id = row.id
            else:
                session_id = (
                    await session.execute(
                        text(
                            "INSERT INTO protection_sessions (device_id, fiber_key,"
                            " sandbox_namespace, current_hsm_state, trigger_origin)"
                            " VALUES (:dev, :fk, :ns, :st, :origin) RETURNING id"
                        ),
                        {
                            "dev": audit.device_id,
                            "fk": audit.fiber_key,
                            "ns": audit.sandbox_namespace,
                            "st": PROTECTION_STATE_AUDITING,
                            "origin": audit.trigger_origin.value,
                        },
                    )
                ).scalar_one()
            await session.execute(_INSERT_AUDIT, {**params, "sid": session_id})

    async def get(
        self, audit_id: UUID, device_id: UUID, sandbox_namespace: str
    ) -> AuditRecord | None:
        from app.core.db import short_session

        async with short_session() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT ca.*, ps.device_id AS dev, ps.sandbox_namespace AS ns,"
                        " ps.fiber_key AS fk, ps.trigger_origin AS origin"
                        " FROM contract_audits ca"
                        " JOIN protection_sessions ps ON ps.id = ca.session_id"
                        " WHERE ca.id = :id AND ps.device_id = :dev AND ps.sandbox_namespace = :ns"
                    ),
                    {"id": audit_id, "dev": device_id, "ns": sandbox_namespace},
                )
            ).first()
        if row is None:
            return None
        ctx = row.masked_extraction_json
        return AuditRecord(
            audit_id=row.id,
            device_id=row.dev,
            sandbox_namespace=row.ns,
            fiber_key=row.fk,
            trigger_origin=TriggerOrigin(row.origin),
            image_count=int(row.image_count),
            verdict_code=row.verdict_code,
            lender_category_resolved=row.lender_category_resolved,
            claimed_brand_normalized=row.claimed_brand_normalized,
            matched_lender_id=row.matched_lender_id,
            nominal_principal_vnd=row.nominal_principal_vnd,
            net_disbursed_vnd=row.net_disbursed_vnd,
            total_repayment_vnd=row.total_repayment_vnd,
            tenure_days=row.tenure_days,
            apr_vn_365=row.apr_vn_365,
            apr_min_bound=row.apr_min_bound,
            apr_max_bound=row.apr_max_bound,
            ear_xirr=row.ear_xirr,
            lost_workdays=row.lost_workdays,
            upfront_fee_detected=row.upfront_fee_detected,
            brand_spoof_detected=row.brand_spoof_detected,
            vietqr_extracted=row.vietqr_extracted,
            user_edited_numbers=row.user_edited_numbers,
            context=json.loads(ctx) if isinstance(ctx, str | bytes) else dict(ctx),
            cited_clauses=tuple(row.cited_legal_clauses or ()),
            latency_ms=int(row.processing_latency_ms),
            model_tier=row.model_tier_used,
            created_at=row.created_at,
        )

    async def update_recalculated(self, audit: AuditRecord) -> None:
        from app.core.db import short_session

        params = _params(audit)
        for key in ("imgs", "brand", "qr", "lat", "tier"):
            params.pop(key)
        async with short_session() as session, session.begin():
            await session.execute(_UPDATE_AUDIT, params)
