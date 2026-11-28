"""Nguồn gốc APK do SERVER lưu (Trạm 1 → Trạm 2, File 2 §1.3 chặng 2).

Client chỉ gửi ``linked_apk_audit_id``; kết quả chữ ký APK (``Provenance.apk_signature_ok``) lấy
lại từ kết quả server đã tính ở ``/audits/apk-verify`` và chỉ hợp lệ cho CHÍNH thiết bị đó — client
không thể tự khai "chữ ký đúng" để hạ phán quyết.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger("vayshield.audit_pipeline.provenance")

APK_RESULT_TTL_S: Final = 24 * 3600
_KEY: Final = "vs:apk:res:"


@dataclass(frozen=True, slots=True)
class ApkProvenance:
    signature_ok: bool | None
    package_name: str | None


NO_PROVENANCE: Final = ApkProvenance(None, None)


async def store_apk_result(
    redis: Redis | None,
    audit_id: UUID,
    device_id: UUID,
    signature_ok: bool | None,
    package_name: str,
) -> None:
    if redis is None:
        return
    payload = json.dumps({"d": str(device_id), "ok": signature_ok, "pkg": package_name})
    try:
        await redis.set(f"{_KEY}{audit_id}", payload, ex=APK_RESULT_TTL_S)
    except Exception:
        logger.warning("apk_result_store_failed")


async def load_apk_result(
    redis: Redis | None, audit_id: UUID | None, device_id: UUID
) -> ApkProvenance:
    if redis is None or audit_id is None:
        return NO_PROVENANCE
    try:
        raw = await redis.get(f"{_KEY}{audit_id}")
    except Exception:
        logger.warning("apk_result_load_failed")
        return NO_PROVENANCE
    if raw is None:
        return NO_PROVENANCE
    try:
        data = json.loads(raw)
        if UUID(data["d"]) != device_id:  # audit của thiết bị khác: bỏ qua
            return NO_PROVENANCE
        ok = data.get("ok")
        return ApkProvenance(ok if isinstance(ok, bool) else None, data.get("pkg"))
    except (ValueError, KeyError, TypeError):
        return NO_PROVENANCE
