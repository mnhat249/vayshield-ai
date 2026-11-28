"""Xác thực người gọi Trạm 1/2 + route giữ body để ký multipart.

``verify_device_request`` (T10) ký trên ``await request.body()``. Với multipart, FastAPI đọc form
TRƯỚC khi chạy dependency nên stream đã bị tiêu thụ → ``RuntimeError: Stream consumed``. Cách
xử lý trong vùng của T11: ``BodyCachingRoute`` gọi ``await request.body()`` (cache vào
``request._body``) trước khi FastAPI parse form; ``request.form()`` sau đó đọc lại từ bộ đệm.
Body đã bị middleware ``BodySizeLimit`` chặn ở mức ASGI (EC-DB05) nên bộ đệm có trần.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from fastapi.routing import APIRoute

from app.core.auth_headers import CallerCredentials, DeviceCaller, DeviceOrSandboxCaller
from app.core.clock import Clock, get_clock
from app.core.errors import AppError, ErrorCode
from app.security.device_auth import (
    DeviceKeyResolver,
    get_device_key_resolver,
    verify_device_request,
)
from app.security.rate_limit import client_ip
from app.services.audit_pipeline.container import Audit, AuditServices
from app.services.realtime.container import SandboxPrincipalResolver, get_sandbox_resolver
from app.services.realtime.models import BorrowerPrincipal


class BodyCachingRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            await request.body()
            return await original(request)

        return handler


async def _authenticate(
    request: Request,
    caller: CallerCredentials,
    services: AuditServices,
    clock: Clock,
    resolver: DeviceKeyResolver,
    sandbox_resolver: SandboxPrincipalResolver | None,
) -> BorrowerPrincipal:
    principal: BorrowerPrincipal | None = None
    if caller.device_signature:
        verified = await verify_device_request(request, caller, clock, resolver)
        principal = BorrowerPrincipal(device_id=verified.device_id)
    elif caller.sandbox_session and sandbox_resolver is not None:
        principal = await sandbox_resolver(caller.sandbox_session)
    if principal is None:
        raise AppError(ErrorCode.UNAUTHORIZED, 401)
    if services.limiter is None:
        if services.strict:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
    else:
        ip = client_ip((request.client.host, request.client.port) if request.client else None)
        await services.limiter.enforce(device_id=principal.device_id, ip=ip, clock=clock)
    return principal


async def device_principal(
    request: Request,
    caller: DeviceCaller,
    services: Audit,
    clock: Annotated[Clock, Depends(get_clock)],
    resolver: Annotated[DeviceKeyResolver, Depends(get_device_key_resolver)],
) -> BorrowerPrincipal:
    """Chỉ chữ ký thiết bị (``/audits/apk-verify``, ``DELETE /devices/me``)."""
    return await _authenticate(request, caller, services, clock, resolver, None)


async def borrower_principal(
    request: Request,
    caller: DeviceOrSandboxCaller,
    services: Audit,
    clock: Annotated[Clock, Depends(get_clock)],
    resolver: Annotated[DeviceKeyResolver, Depends(get_device_key_resolver)],
    sandbox_resolver: Annotated[SandboxPrincipalResolver, Depends(get_sandbox_resolver)],
) -> BorrowerPrincipal:
    return await _authenticate(request, caller, services, clock, resolver, sandbox_resolver)


Authenticate = Callable[[], Awaitable[BorrowerPrincipal]]


async def deferred_borrower_auth(
    request: Request,
    caller: DeviceOrSandboxCaller,
    services: Audit,
    clock: Annotated[Clock, Depends(get_clock)],
    resolver: Annotated[DeviceKeyResolver, Depends(get_device_key_resolver)],
    sandbox_resolver: Annotated[SandboxPrincipalResolver, Depends(get_sandbox_resolver)],
) -> Authenticate:
    """Như ``borrower_principal`` nhưng kiểm chữ ký được HOÃN tới khi handler gọi.

    Dùng cho route multipart: handler từ chối sớm ảnh sai loại/quá số lượng (415/422, chỉ nhìn
    header + metadata, chưa đọc nội dung) rồi mới tốn công verify ECDSA. Việc kiểm tra SỰ CÓ MẶT
    của header xác thực vẫn chạy ở đây (thiếu → 401 ngay).
    """

    async def authenticate() -> BorrowerPrincipal:
        return await _authenticate(request, caller, services, clock, resolver, sandbox_resolver)

    return authenticate


DevicePrincipal = Annotated[BorrowerPrincipal, Depends(device_principal)]
BorrowerCaller = Annotated[BorrowerPrincipal, Depends(borrower_principal)]
DeferredAuth = Annotated[Authenticate, Depends(deferred_borrower_auth)]
