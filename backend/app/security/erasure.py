"""Xóa dữ liệu theo yêu cầu (Luật 91/2025 — File 4 §5.2 mục 4, EC-DB21).

``DELETE /api/v1/devices/me`` → ``erase_device``:

1. Phiếu đóng góp ẩn danh trên đồ thị mồi (``mule_incident_edges``) KHÔNG bị xóa (nếu xóa, kẻ lừa
   đảo có thể "rửa" quorum bằng cách xóa thiết bị) mà chuyển sang 1 thiết bị bia mộ
   (``Tombstone Anonymized Hash``): id = 128 bit đầu của ``SHA-256(salt_ngẫu_nhiên || device_id)``,
   salt bị hủy ngay → không ai (kể cả VayShield) liên kết ngược được. Mọi phiếu của cùng thiết bị
   dùng chung 1 bia mộ nên ``UNIQUE (mule_node_id, reporter_device_id)`` và đếm quorum giữ nguyên.
   Liên kết tới audit/intercept nguồn (sẽ bị xóa) đặt NULL.
2. ``b2b_disbursement_shields.matched_session_id`` (không ON DELETE) đặt NULL theo từng tenant
   (bảng có RLS FORCE, role app phải đặt ``app.current_partner_id``).
3. ``DELETE anonymous_devices`` → ``ON DELETE CASCADE`` xóa phiên, audit, intercept, liên kết
   người thân (cipher Web Push/SMS).
4. Ghi ``tamper_evident_audit_chain`` với ``target_ref_id`` = id bia mộ (không ghi device_id thật).
5. SAU KHI PG COMMIT: xóa khóa Redis gắn thiết bị (``vs:shield:state:{id}``,
   ``vs:idem:notif:{id}:*``, ``vs:rl:dev:{id}``, ``vs:sig:dev:{id}:*``) bằng ``erase_device_redis``
   — ``erase_device_and_commit`` làm đúng thứ tự này.

Không log ``device_id`` (chỉ log số lượng).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core.logging import get_logger
from app.security.rate_limit import device_bucket_key

logger = get_logger("vayshield.security.erasure")

ERASURE_ACTOR: Final = "system:data-erasure"
ERASURE_ACTION: Final = "DEVICE_ERASED_LAW91"

_SQL_LOCK_DEVICE: Final = "SELECT platform_type FROM anonymous_devices WHERE id = :dev FOR UPDATE"
_SQL_COUNT_EDGES: Final = "SELECT count(*) FROM mule_incident_edges WHERE reporter_device_id = :dev"
_SQL_INSERT_TOMBSTONE: Final = (
    "INSERT INTO anonymous_devices (id, pubkey_ecdsa_p256, phone_blind_hmac, platform_type)"
    " VALUES (:tomb, NULL, NULL, :platform)"
)
_SQL_REASSIGN_EDGES: Final = (
    "UPDATE mule_incident_edges SET reporter_device_id = :tomb,"
    " source_audit_id = NULL, source_intercept_id = NULL"
    " WHERE reporter_device_id = :dev"
)
_SQL_DETACH_AUDIT_REFS: Final = (
    "UPDATE mule_incident_edges SET source_audit_id = NULL WHERE source_audit_id IN ("
    " SELECT ca.id FROM contract_audits ca JOIN protection_sessions ps ON ps.id = ca.session_id"
    " WHERE ps.device_id = :dev)"
)
_SQL_DETACH_INTERCEPT_REFS: Final = (
    "UPDATE mule_incident_edges SET source_intercept_id = NULL WHERE source_intercept_id IN ("
    " SELECT ie.id FROM interception_events ie JOIN protection_sessions ps ON ps.id = ie.session_id"
    " WHERE ps.device_id = :dev)"
)
_SQL_LIST_PARTNERS: Final = "SELECT id FROM fintech_partners"
_SQL_SET_PARTNER: Final = "SELECT set_config('app.current_partner_id', :pid, true)"
_SQL_DETACH_B2B: Final = (
    "UPDATE b2b_disbursement_shields SET matched_session_id = NULL"
    " WHERE matched_session_id IN (SELECT id FROM protection_sessions WHERE device_id = :dev)"
)
_SQL_DELETE_DEVICE: Final = "DELETE FROM anonymous_devices WHERE id = :dev"
_SQL_AUDIT_CHAIN: Final = "SELECT append_audit_chain(:actor, :action, :target)"


@dataclass(frozen=True, slots=True)
class ErasureReceipt:
    erased: bool
    tombstone_id: UUID | None
    contributions_preserved: int
    redis_keys_deleted: int


def tombstone_id_for(device_id: UUID, *, salt: bytes | None = None) -> UUID:
    """Id bia mộ một chiều: ``SHA-256(salt || device_id)[:16]`` với salt 256-bit ngẫu nhiên bị hủy.

    ``salt`` chỉ để test tất định; code thật luôn để ``None``.
    """
    material = (salt if salt is not None else os.urandom(32)) + device_id.bytes
    digest = bytearray(hashlib.sha256(material).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x80  # version 8 (custom, RFC 9562)
    digest[8] = (digest[8] & 0x3F) | 0x80  # variant RFC
    return UUID(bytes=bytes(digest))


async def erase_device_rows(
    conn: AsyncConnection | AsyncSession, device_id: UUID
) -> tuple[bool, UUID | None, int]:
    """Phần PostgreSQL, gọi TRONG 1 transaction (caller commit).

    Trả ``(erased, tombstone_id, số phiếu giữ lại)``.
    """
    params = {"dev": device_id}
    row = (await conn.execute(text(_SQL_LOCK_DEVICE), params)).first()
    if row is None:
        return False, None, 0
    platform = str(row[0])
    n_edges = int((await conn.execute(text(_SQL_COUNT_EDGES), params)).scalar_one())
    tombstone: UUID | None = None
    await conn.execute(text(_SQL_DETACH_AUDIT_REFS), params)
    await conn.execute(text(_SQL_DETACH_INTERCEPT_REFS), params)
    if n_edges > 0:
        tombstone = tombstone_id_for(device_id)
        await conn.execute(text(_SQL_INSERT_TOMBSTONE), {"tomb": tombstone, "platform": platform})
        await conn.execute(text(_SQL_REASSIGN_EDGES), {"tomb": tombstone, "dev": device_id})
    partners = (await conn.execute(text(_SQL_LIST_PARTNERS))).scalars().all()
    for pid in partners:
        await conn.execute(text(_SQL_SET_PARTNER), {"pid": str(pid)})
        await conn.execute(text(_SQL_DETACH_B2B), params)
    if partners:
        await conn.execute(text(_SQL_SET_PARTNER), {"pid": ""})
    await conn.execute(text(_SQL_DELETE_DEVICE), params)
    audit_target = tombstone if tombstone is not None else tombstone_id_for(device_id)
    await conn.execute(
        text(_SQL_AUDIT_CHAIN),
        {"actor": ERASURE_ACTOR, "action": ERASURE_ACTION, "target": audit_target},
    )
    return True, tombstone, n_edges


async def erase_device_redis(redis: Redis, device_id: UUID) -> int:
    keys: list[str] = [f"vs:shield:state:{device_id}", device_bucket_key(device_id)]
    for pattern in (f"vs:idem:notif:{device_id}:*", f"vs:sig:dev:{device_id}:*"):
        async for key in redis.scan_iter(match=pattern, count=500):
            keys.append(key if isinstance(key, str) else key.decode())
    return int(await redis.delete(*keys))


async def erase_device(conn: AsyncConnection | AsyncSession, device_id: UUID) -> ErasureReceipt:
    """CHỈ phần PostgreSQL, trong transaction của caller. KHÔNG đụng Redis.

    Caller phải COMMIT rồi mới gọi ``erase_device_redis`` (nếu PG rollback mà Redis đã xóa thì
    trạng thái Khiên nóng mất trong khi thiết bị vẫn còn). Dùng ``erase_device_and_commit`` cho
    đúng thứ tự.
    """
    erased, tombstone, n_edges = await erase_device_rows(conn, device_id)
    logger.info("erasure: erased=%s contributions_preserved=%d", erased, n_edges)
    return ErasureReceipt(
        erased=erased,
        tombstone_id=tombstone,
        contributions_preserved=n_edges,
        redis_keys_deleted=0,
    )


async def erase_device_and_commit(
    engine: AsyncEngine, redis: Redis | None, device_id: UUID
) -> ErasureReceipt:
    """PG trong 1 transaction → COMMIT → sau đó mới xóa khóa Redis."""
    async with engine.begin() as conn:
        receipt = await erase_device(conn, device_id)
    if redis is None or not receipt.erased:
        return receipt
    try:
        deleted = await erase_device_redis(redis, device_id)
    except Exception:
        # PG đã commit (dữ liệu bền vững đã xóa); khóa Redis đều có TTL nên tự hết hạn.
        logger.warning("erasure: xóa khóa Redis lỗi sau commit — để TTL tự dọn")
        return receipt
    logger.info("erasure: redis_keys_deleted=%d", deleted)
    return replace(receipt, redis_keys_deleted=deleted)
