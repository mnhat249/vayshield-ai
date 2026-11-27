"""Dựng và cung cấp các dịch vụ realtime/Trạm 3 cho route (không sửa ``main.py``, D5).

``get_services`` tạo bộ dịch vụ MỘT LẦN cho mỗi app và cache ở ``app.state.realtime_services``.
Test (hoặc T16) có thể gán sẵn ``app.state.realtime_services`` bằng bản có fake.

Xác thực người gọi (``borrower_principal`` / ``realtime_principal``): chữ ký thiết bị được kiểm
bằng ``app.security.device_auth.verify_device_request`` (T10); phiên Sandbox đi qua
``SandboxPrincipalResolver`` (mặc định từ chối — T16 ghi đè bằng ``get_sandbox_resolver``); mọi
lời gọi đều qua ``CompositeRateLimiter`` (EC-DB25).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI
from fastapi.requests import HTTPConnection
from redis.asyncio import Redis

from app.core import redis as core_redis
from app.core.auth_headers import (
    CallerCredentials,
    DeviceOrSandboxCaller,
    RealtimeCaller,
    require_guardian,
)
from app.core.clock import Clock, get_clock
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.security.device_auth import (
    DeviceKeyResolver,
    get_device_key_resolver,
    verify_device_request,
)
from app.security.rate_limit import CompositeRateLimiter, client_ip
from app.services.realtime.escalation import EscalationService, Sleeper
from app.services.realtime.hub import RealtimeHub
from app.services.realtime.models import BorrowerPrincipal, GuardianLink
from app.services.realtime.pairing import PairingService
from app.services.realtime.persistence import (
    NullShieldPersistence,
    PgFallbackReader,
    PgShieldPersistence,
    ShieldPersistence,
)
from app.services.realtime.repository import (
    FamilyRepository,
    InMemoryFamilyRepository,
    SqlFamilyRepository,
)
from app.services.realtime.rescue import RescueService
from app.services.realtime.senders import (
    PushSender,
    SmsSender,
    build_push_sender,
    build_sms_sender,
)
from app.services.realtime.shield_service import ShieldService
from app.shield_state.store import FallbackReader, ShieldStateStore

logger = get_logger("vayshield.realtime.container")


def conn_ip(conn: HTTPConnection) -> str:
    return client_ip((conn.client.host, conn.client.port) if conn.client else None)


SandboxPrincipalResolver = Callable[[str], Awaitable[BorrowerPrincipal | None]]


@dataclass
class RealtimeServices:
    redis: Redis
    clock: Clock
    settings: Settings
    repo: FamilyRepository
    hub: RealtimeHub
    escalation: EscalationService
    pairing: PairingService
    rescue: RescueService
    shield: ShieldService
    limiter: CompositeRateLimiter
    ws_ping_interval_s: float = 20
    ack_timeout_s: int = 10

    async def startup(self) -> None:
        """Gọi trong lifespan: bật hub pub/sub và vòng quét hạn ACK."""
        # Sweeper trước và độc lập: Redis chưa sẵn sàng không được làm mất SMS fallback (S2-EC44).
        await self.escalation.start()
        try:
            await self.hub.start()
        except Exception:
            logger.exception("hub_start_failed_retrying_in_background")
            self.hub.start_in_background()

    async def shutdown(self) -> None:
        """Gọi trong lifespan khi tắt: hủy hẹn giờ/sweeper và đóng pub/sub."""
        await self.escalation.aclose()
        await self.hub.stop()


def build_services(
    *,
    redis: Redis,
    clock: Clock,
    settings: Settings,
    repo: FamilyRepository | None = None,
    push: PushSender | None = None,
    sms: SmsSender | None = None,
    sleeper: Sleeper | None = None,
    sweep_interval_s: float | None = 2.0,
    persistence: ShieldPersistence | None = None,
    fallback: FallbackReader | None = None,
) -> RealtimeServices:
    has_db = settings.database_url is not None
    family: FamilyRepository = repo or (
        SqlFamilyRepository() if has_db else InMemoryFamilyRepository()
    )
    hub = RealtimeHub(redis, clock, ping_interval_s=settings.ws_ping_interval_s)
    escalation_kwargs: dict[str, Any] = {}
    if sleeper is not None:
        escalation_kwargs["sleeper"] = sleeper
    escalation = EscalationService(
        redis=redis,
        clock=clock,
        repo=family,
        hub=hub,
        push=push or build_push_sender(settings),
        sms=sms or build_sms_sender(settings),
        ack_timeout_s=settings.escalation_ack_timeout_s,
        sweep_interval_s=sweep_interval_s,
        **escalation_kwargs,
    )
    # FallbackReader thật khi có PostgreSQL (EC-DB11); không có DB → None (store coi là degraded).
    reader: FallbackReader | None = fallback or (PgFallbackReader() if has_db else None)
    store = ShieldStateStore(redis, fallback=reader)
    shield = ShieldService(
        redis=redis,
        clock=clock,
        store=store,
        escalation=escalation,
        persistence=persistence or (PgShieldPersistence() if has_db else NullShieldPersistence()),
    )
    return RealtimeServices(
        redis=redis,
        clock=clock,
        settings=settings,
        repo=family,
        hub=hub,
        escalation=escalation,
        pairing=PairingService(
            redis=redis,
            clock=clock,
            repo=family,
            code_ttl_s=settings.pairing_code_ttl_s,
            max_fail_attempts=settings.pairing_max_fail_attempts,
            session_ttl_s=settings.guardian_session_ttl_s,
        ),
        rescue=RescueService(redis=redis, clock=clock, hub=hub),
        shield=shield,
        limiter=CompositeRateLimiter.from_settings(redis, settings),
        ws_ping_interval_s=settings.ws_ping_interval_s,
        ack_timeout_s=settings.escalation_ack_timeout_s,
    )


async def get_services(
    conn: HTTPConnection, clock: Annotated[Clock, Depends(get_clock)]
) -> RealtimeServices:
    existing: RealtimeServices | None = getattr(conn.app.state, "realtime_services", None)
    if existing is not None:
        return existing
    services = build_services(redis=core_redis.get_redis(), clock=clock, settings=get_settings())
    conn.app.state.realtime_services = services
    return services


async def start_realtime(app: FastAPI) -> None:
    """Gọi trong lifespan: dựng dịch vụ + bật hub pub/sub và sweeper ACK ngay khi khởi động (pod
    mới sau restart phải quét ``vs:ack:due``, S2-EC44). Không có Redis (LOCAL/TEST) → bỏ qua."""
    existing: RealtimeServices | None = getattr(app.state, "realtime_services", None)
    if existing is None:
        redis = core_redis.init_redis()
        if redis is None:
            return
        existing = build_services(redis=redis, clock=get_clock(), settings=get_settings())
        app.state.realtime_services = existing
    try:
        await existing.startup()
    except Exception:  # Redis chưa sẵn sàng không được làm app không khởi động; sẽ bật lười sau
        logger.exception("realtime_startup_failed")


async def stop_realtime(app: FastAPI) -> None:
    services: RealtimeServices | None = getattr(app.state, "realtime_services", None)
    if services is not None:
        await services.shutdown()


Services = Annotated[RealtimeServices, Depends(get_services)]


async def _default_sandbox_resolver(_: str) -> BorrowerPrincipal | None:
    return None


def get_sandbox_resolver() -> SandboxPrincipalResolver:
    """Dependency để T16 (Sandbox) ghi đè; mặc định không nhận phiên Sandbox nào."""
    return _default_sandbox_resolver


async def authenticate_borrower(
    conn: HTTPConnection,
    caller: CallerCredentials,
    services: RealtimeServices,
    clock: Clock,
    resolver: DeviceKeyResolver,
    sandbox_resolver: SandboxPrincipalResolver,
) -> BorrowerPrincipal:
    principal: BorrowerPrincipal | None = None
    if caller.device_signature:
        from fastapi import Request

        if not isinstance(conn, Request):  # chỉ route HTTP mới có body để ký
            raise AppError(ErrorCode.UNAUTHORIZED, 401)
        verified = await verify_device_request(conn, caller, clock, resolver)
        principal = BorrowerPrincipal(device_id=verified.device_id)
    elif caller.sandbox_session:
        principal = await sandbox_resolver(caller.sandbox_session)
    if principal is None:
        raise AppError(ErrorCode.UNAUTHORIZED, 401)
    await services.limiter.enforce(
        device_id=principal.device_id,
        ip=conn_ip(conn),
        clock=clock,
    )
    return principal


async def borrower_principal(
    conn: HTTPConnection,
    caller: DeviceOrSandboxCaller,
    services: Services,
    clock: Annotated[Clock, Depends(get_clock)],
    resolver: Annotated[DeviceKeyResolver, Depends(get_device_key_resolver)],
    sandbox_resolver: Annotated[SandboxPrincipalResolver, Depends(get_sandbox_resolver)],
) -> BorrowerPrincipal:
    return await authenticate_borrower(conn, caller, services, clock, resolver, sandbox_resolver)


async def guardian_link(
    conn: HTTPConnection,
    caller: Annotated[CallerCredentials, Depends(require_guardian)],
    services: Services,
    clock: Annotated[Clock, Depends(get_clock)],
) -> GuardianLink:
    session = await services.pairing.resolve_session(caller.guardian_token)
    await services.limiter.enforce(
        device_id=session.link.link_id,
        ip=conn_ip(conn),
        clock=clock,
    )
    return session.link


BorrowerCaller = Annotated[BorrowerPrincipal, Depends(borrower_principal)]
GuardianLinkCaller = Annotated[GuardianLink, Depends(guardian_link)]


async def realtime_identity(
    conn: HTTPConnection,
    caller: RealtimeCaller,
    services: Services,
    clock: Annotated[Clock, Depends(get_clock)],
    resolver: Annotated[DeviceKeyResolver, Depends(get_device_key_resolver)],
    sandbox_resolver: Annotated[SandboxPrincipalResolver, Depends(get_sandbox_resolver)],
) -> BorrowerPrincipal | GuardianLink:
    """Người gọi ``/ws-ticket``: người thân (bearer) hoặc người vay (chữ ký thiết bị/Sandbox)."""
    if caller.guardian_token:
        session = await services.pairing.resolve_session(caller.guardian_token)
        await services.limiter.enforce(
            device_id=session.link.link_id,
            ip=conn_ip(conn),
            clock=clock,
        )
        return session.link
    return await authenticate_borrower(conn, caller, services, clock, resolver, sandbox_resolver)


RealtimeIdentity = Annotated[BorrowerPrincipal | GuardianLink, Depends(realtime_identity)]
