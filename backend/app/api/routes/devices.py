"""STUB M0 — Edge: đăng ký thiết bị & xóa dữ liệu. Logic thật: T11 (giữ nguyên path/schema)."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core import mocks
from app.core.auth_headers import DeviceCaller
from app.core.openapi import error_responses
from app.core.schemas.devices import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceWipeResponse,
)
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/devices", tags=["edge-policy"])


@router.post(
    "/register",
    operation_id="devicesRegister",
    status_code=status.HTTP_201_CREATED,
    response_model=DeviceRegisterResponse,
    responses=error_responses(400),
    summary="Đăng ký thiết bị ẩn danh + khóa ECDSA (Public + PoW / RateLimit, < 80ms)",
)
@stub
async def register_device(body: DeviceRegisterRequest) -> DeviceRegisterResponse:
    return mocks.DEVICE_REGISTER.model_copy(
        update={"daily_wage_vnd": body.daily_wage_vnd or 320_000}
    )


@router.delete(
    "/me",
    operation_id="devicesDeleteMe",
    response_model=DeviceWipeResponse,
    responses=error_responses(401),
    summary="Xóa toàn bộ dữ liệu & hủy liên kết người thân (Luật 91/2025, EC-DB21)",
)
@stub
async def delete_my_device(_caller: DeviceCaller) -> DeviceWipeResponse:
    return mocks.DEVICE_WIPE
