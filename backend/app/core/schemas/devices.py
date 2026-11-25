"""Edge & Policy: đăng ký thiết bị ẩn danh, xóa dữ liệu (File 4 §4.1, §5.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.schemas.common import PlatformType, RequestModel


class ConsentToggles(RequestModel):
    """3 công tắc đồng thuận tách biệt theo mục đích (Luật 91/2025, File 4 §5.2)."""

    contract_scan: bool = Field(description="(a) Soi hợp đồng khi chủ động gửi ảnh")
    shield_monitoring: bool = Field(description="(b) Bật Khiên theo dõi giải ngân cục bộ")
    family_link: bool = Field(default=False, description="(c) Ghép đôi người thân — mặc định Tắt")


class ProofOfWork(RequestModel):
    """Hashcash: sha256(public_key_spki_b64 + ':' + timestamp + ':' + nonce) có đủ bit 0 đầu."""

    timestamp: int = Field(ge=0, description="Unix giây")
    nonce: str = Field(min_length=1, max_length=64)


class DeviceRegisterRequest(RequestModel):
    public_key_spki_b64: str = Field(
        min_length=40, max_length=600, description="Khóa công khai ECDSA P-256 (SPKI DER, base64)"
    )
    platform_type: PlatformType
    app_version: str = Field(min_length=1, max_length=32)
    daily_wage_vnd: int | None = Field(default=None, ge=50_000, le=10_000_000)
    consents: ConsentToggles
    phone_e164_optin: str | None = Field(
        default=None,
        pattern=r"^\+[1-9]\d{7,14}$",
        description="SĐT E.164 tùy chọn để khớp webhook B2B; server chỉ lưu blind index HMAC",
    )
    proof_of_work: ProofOfWork | None = None


class SignedEpochOffset(BaseModel):
    """Giờ máy chủ có ký (File 2 §1.1 SignedServerEpochOffset)."""

    server_time_ms: int
    key_id: str
    signature_b64: str


class DeviceRegisterResponse(BaseModel):
    device_id: UUID
    policy_version: int
    daily_wage_vnd: int
    phone_optin_expires_at: datetime | None = None
    server_epoch: SignedEpochOffset


class DeviceWipeResponse(BaseModel):
    """Kết quả xóa dữ liệu 1 chạm (EC-DB21)."""

    status: Literal["WIPED"]
    wiped_at: datetime
    family_links_revoked: int = Field(ge=0)
    mule_edges_tombstoned: int = Field(ge=0)
