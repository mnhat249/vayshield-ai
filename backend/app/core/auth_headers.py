"""Khai báo các cơ chế xác thực trong contract (security schemes) + trích xuất header.

File này CHỈ kiểm tra *sự có mặt* của thông tin xác thực để OpenAPI mô tả đúng và để mock
trả 401 giống thật. Kiểm tra mật mã (ECDSA thiết bị, HMAC B2B, JWT ops, vé WS) nằm ở
``app/security`` (T10) và được route gọi thêm sau các dependency này.

Định dạng header (khóa ở contract M0):

- ``X-VayShield-Device-Signature``: ``v1;device_id=<uuid>;ts=<unix_s>;sig=<base64url>`` với
  ``sig = ECDSA_P256_SHA256(device_key, METHOD + "\\n" + PATH?QUERY + "\\n" + ts + "\\n" +
  hex(sha256(raw_body)))``.
- ``X-Sandbox-Session``: token phiên sandbox do ``POST /api/v1/sandbox/sessions`` cấp.
- ``Authorization: Bearer <guardian_session_token>`` cho người thân (sau pair-confirm).
- ``Authorization: Bearer <JWT>`` role ``FRAUD_ANALYST`` cho Ops.
- B2B: ``X-VayShield-Partner-Key`` + ``X-VayShield-Signature: sha256=<hex>`` +
  ``X-VayShield-Timestamp`` + ``Idempotency-Key`` (File 4 §4.2(B)).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AppError, ErrorCode

DEVICE_SIGNATURE_HEADER: Final = "X-VayShield-Device-Signature"
SANDBOX_SESSION_HEADER: Final = "X-Sandbox-Session"
PARTNER_KEY_HEADER: Final = "X-VayShield-Partner-Key"
PARTNER_SIGNATURE_HEADER: Final = "X-VayShield-Signature"
PARTNER_TIMESTAMP_HEADER: Final = "X-VayShield-Timestamp"
IDEMPOTENCY_KEY_HEADER: Final = "Idempotency-Key"

device_signature_scheme = APIKeyHeader(
    name=DEVICE_SIGNATURE_HEADER,
    scheme_name="DeviceSignature",
    description="Chữ ký ECDSA P-256 của thiết bị ẩn danh (xem mô tả định dạng trong contract).",
    auto_error=False,
)
sandbox_session_scheme = APIKeyHeader(
    name=SANDBOX_SESSION_HEADER,
    scheme_name="SandboxSession",
    description="Token phiên Sandbox ephemeral (mỗi tab một namespace, EC-DB16).",
    auto_error=False,
)
guardian_bearer_scheme = HTTPBearer(
    scheme_name="GuardianSession",
    description="Guardian session token cấp tại /family/pair-confirm.",
    auto_error=False,
)
ops_bearer_scheme = HTTPBearer(
    scheme_name="OpsJwt",
    bearerFormat="JWT",
    description="JWT RBAC role FRAUD_ANALYST cho bàn làm việc Fraud Ops.",
    auto_error=False,
)
partner_key_scheme = APIKeyHeader(
    name=PARTNER_KEY_HEADER,
    scheme_name="PartnerKey",
    description="Khóa định danh đối tác B2B, vd vsp_live_xxx.",
    auto_error=False,
)
partner_signature_scheme = APIKeyHeader(
    name=PARTNER_SIGNATURE_HEADER,
    scheme_name="PartnerHmacSignature",
    description="sha256=HEX(HMAC_SHA256(partner_secret, timestamp + '.' + raw_body)).",
    auto_error=False,
)


def _unauthorized(message_vi: str | None = None) -> AppError:
    return AppError(ErrorCode.UNAUTHORIZED, 401, message_vi)


@dataclass(frozen=True, slots=True)
class CallerCredentials:
    """Thông tin xác thực thô (chưa kiểm mật mã)."""

    device_signature: str | None = None
    sandbox_session: str | None = None
    guardian_token: str | None = None

    @property
    def is_sandbox(self) -> bool:
        return self.sandbox_session is not None


async def require_device(
    device_signature: Annotated[str | None, Security(device_signature_scheme)],
) -> CallerCredentials:
    if not device_signature:
        raise _unauthorized("Thiếu chữ ký thiết bị.")
    return CallerCredentials(device_signature=device_signature)


async def require_device_or_sandbox(
    device_signature: Annotated[str | None, Security(device_signature_scheme)],
    sandbox_session: Annotated[str | None, Security(sandbox_session_scheme)],
) -> CallerCredentials:
    if not device_signature and not sandbox_session:
        raise _unauthorized("Thiếu chữ ký thiết bị hoặc phiên Sandbox.")
    return CallerCredentials(
        device_signature=device_signature or None, sandbox_session=sandbox_session or None
    )


async def require_sandbox(
    sandbox_session: Annotated[str | None, Security(sandbox_session_scheme)],
) -> CallerCredentials:
    if not sandbox_session:
        raise _unauthorized("Thiếu phiên Sandbox (X-Sandbox-Session).")
    return CallerCredentials(sandbox_session=sandbox_session)


async def require_guardian(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(guardian_bearer_scheme)],
) -> CallerCredentials:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Thiếu phiên người thân.")
    return CallerCredentials(guardian_token=credentials.credentials)


async def require_realtime_principal(
    device_signature: Annotated[str | None, Security(device_signature_scheme)],
    sandbox_session: Annotated[str | None, Security(sandbox_session_scheme)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(guardian_bearer_scheme)],
) -> CallerCredentials:
    """Người vay (chữ ký thiết bị), người thân (bearer) hoặc phiên sandbox."""
    guardian = credentials.credentials if credentials is not None else None
    if not device_signature and not sandbox_session and not guardian:
        raise _unauthorized()
    return CallerCredentials(
        device_signature=device_signature or None,
        sandbox_session=sandbox_session or None,
        guardian_token=guardian or None,
    )


@dataclass(frozen=True, slots=True)
class OpsCredentials:
    bearer_token: str


async def require_ops(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(ops_bearer_scheme)],
) -> OpsCredentials:
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Thiếu JWT Fraud Ops.")
    return OpsCredentials(bearer_token=credentials.credentials)


@dataclass(frozen=True, slots=True)
class PartnerCredentials:
    partner_key: str
    signature: str


async def require_partner(
    partner_key: Annotated[str | None, Security(partner_key_scheme)],
    signature: Annotated[str | None, Security(partner_signature_scheme)],
) -> PartnerCredentials:
    if not partner_key or not signature:
        raise _unauthorized("Thiếu khóa đối tác hoặc chữ ký HMAC.")
    return PartnerCredentials(partner_key=partner_key, signature=signature)


DeviceCaller = Annotated[CallerCredentials, Depends(require_device)]
DeviceOrSandboxCaller = Annotated[CallerCredentials, Depends(require_device_or_sandbox)]
SandboxCaller = Annotated[CallerCredentials, Depends(require_sandbox)]
GuardianCaller = Annotated[CallerCredentials, Depends(require_guardian)]
RealtimeCaller = Annotated[CallerCredentials, Depends(require_realtime_principal)]
OpsCaller = Annotated[OpsCredentials, Depends(require_ops)]
PartnerCaller = Annotated[PartnerCredentials, Depends(require_partner)]
