"""Edge: đăng ký thiết bị ẩn danh & xóa dữ liệu (File 4 §4.1, §5.2; EC-DB21). Logic: T11."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.core.openapi import error_responses
from app.core.schemas.devices import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceWipeResponse,
)
from app.security.rate_limit import client_ip
from app.services.audit_pipeline.auth import DevicePrincipal
from app.services.audit_pipeline.container import Audit
from app.services.audit_pipeline.registration import register_device

router = APIRouter(prefix="/api/v1/devices", tags=["edge-policy"])


@router.post(
    "/register",
    operation_id="devicesRegister",
    status_code=status.HTTP_201_CREATED,
    response_model=DeviceRegisterResponse,
    responses=error_responses(400),
    summary="Đăng ký thiết bị ẩn danh + khóa ECDSA (Public + PoW / RateLimit, < 80ms)",
)
async def register(
    body: DeviceRegisterRequest, request: Request, services: Audit
) -> DeviceRegisterResponse:
    ip = client_ip((request.client.host, request.client.port) if request.client else None)
    return await register_device(
        body,
        settings=services.settings,
        clock=services.clock,
        repo=services.devices,
        signer=services.signer,
        indexer=services.indexer,
        redis=services.redis,
        ip=ip,
        strict=services.strict,
    )


@router.delete(
    "/me",
    operation_id="devicesDeleteMe",
    response_model=DeviceWipeResponse,
    responses=error_responses(401),
    summary="Xóa toàn bộ dữ liệu & hủy liên kết người thân (Luật 91/2025, EC-DB21)",
)
async def delete_my_device(who: DevicePrincipal, services: Audit) -> DeviceWipeResponse:
    links, edges = await services.devices.erase(who.device_id, services.redis)
    return DeviceWipeResponse(
        status="WIPED",
        wiped_at=services.clock.now(),
        family_links_revoked=links,
        mule_edges_tombstoned=edges,
    )
