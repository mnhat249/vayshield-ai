"""Dựng và cung cấp dịch vụ Trạm 1 & 2 cho route (không sửa ``main.py``, D5).

``get_audit_services`` tạo bộ dịch vụ MỘT LẦN cho mỗi app (cache ``app.state.audit_services``);
test gán sẵn bản có fake.

READINESS (yêu cầu của T09 cho ``PgLenderRegistry``): registry rỗng/chưa nạp sẽ âm thầm tắt mọi
tín hiệu mạo danh, nên ``ensure_ready()`` chạy ở ĐẦU mọi request cần registry: chưa ``loaded`` thì
``refresh()`` ngay (khóa chống nạp song song); thất bại → 503 ``SERVICE_DEGRADED`` (fail-closed),
KHÔNG BAO GIỜ phục vụ bằng registry rỗng. ``start_audit_pipeline(app)`` nạp sớm lúc khởi động và
``readiness()`` báo trạng thái cho health/ops — đấu vào lifespan thuộc ``main.py`` (ngoài vùng của
T11, ghi trong "Cần quyết định"); chưa đấu thì nạp lười ở request đầu vẫn an toàn.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.requests import HTTPConnection
from pydantic import SecretStr
from redis.asyncio import Redis

from ai_pipeline.extraction import ExtractionPipeline
from ai_pipeline.extraction.cascade import CascadeConfig, ModelCascade
from ai_pipeline.extraction.gemini_client import ApiKeyPool, GeminiClient
from app.core import redis as core_redis
from app.core.clock import Clock, get_clock
from app.core.config import AppEnv, Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.security.blind_index import BlindIndexer
from app.security.rate_limit import CompositeRateLimiter
from app.security.signing import Ed25519Signer
from app.services.audit_pipeline.apk import ApkTrustStore, load_apk_trust
from app.services.audit_pipeline.ingest import CpuPool
from app.services.audit_pipeline.ports import (
    LegalExplainer,
    MuleGraphPort,
    MulePort,
    NullLegalExplainer,
    NullMulePort,
    NullShieldHook,
    RagLegalExplainer,
    ShieldHook,
    SpeechToText,
    StoreShieldHook,
)
from app.services.audit_pipeline.repository import (
    AuditRepository,
    DeviceRepository,
    InMemoryAuditRepository,
    InMemoryDeviceRepository,
    SqlAuditRepository,
    SqlDeviceRepository,
)
from app.verdict import LenderRegistry, PgLenderRegistry
from app.verdict.registry import LenderRecord

logger = get_logger("vayshield.audit_pipeline.container")


class UnconfiguredRegistry:
    """Registry khi không có ``DATABASE_URL``: luôn chưa nạp → fail-closed (không rỗng im lặng)."""

    loaded = False

    def active_records(self) -> Sequence[LenderRecord]:
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503)

    def get(self, entity_code: str) -> LenderRecord | None:
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503)


@dataclass
class AuditServices:
    settings: Settings
    clock: Clock
    registry: LenderRegistry
    devices: DeviceRepository
    audits: AuditRepository
    legal: LegalExplainer
    mule: MulePort
    shield: ShieldHook
    cpu: CpuPool
    trust: ApkTrustStore = field(default_factory=ApkTrustStore)
    pipeline: ExtractionPipeline | None = None
    vip_pipeline: ExtractionPipeline | None = None
    stt: SpeechToText | None = None
    signer: Ed25519Signer | None = None
    indexer: BlindIndexer | None = None
    limiter: CompositeRateLimiter | None = None
    redis: Redis | None = None
    refresher: Callable[[], Awaitable[None]] | None = None
    trust_loaded: bool = True  # False tới khi sổ chữ ký APK nạp xong (đường PostgreSQL)
    refresh_ttl_s: float = 300.0
    _refreshed_at: float | None = field(default=None, repr=False)
    _bg_refresh: asyncio.Task[None] | None = field(default=None, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def strict(self) -> bool:
        """Ngoài LOCAL/TEST thiếu Redis/khóa là lỗi cấu hình → fail-closed."""
        return self.settings.app_env not in (AppEnv.LOCAL, AppEnv.TEST)

    @property
    def loaded(self) -> bool:
        return bool(getattr(self.registry, "loaded", True)) and self.trust_loaded

    def readiness(self) -> dict[str, bool]:
        return {
            "lender_registry": bool(getattr(self.registry, "loaded", True)),
            "apk_trust": self.trust_loaded,
            "policy_signer": self.signer is not None,
        }

    def _maybe_refresh_in_background(self) -> None:
        """Nạp lại theo chu kỳ (registry_pg: 'và theo chu kỳ'); lỗi giữ snapshot cũ."""
        if self.refresher is None:
            return
        now = self.clock.monotonic()
        if self._refreshed_at is None:
            self._refreshed_at = now
            return
        busy = self._bg_refresh is not None and not self._bg_refresh.done()
        if busy or now - self._refreshed_at < self.refresh_ttl_s:
            return
        self._refreshed_at = now
        refresher = self.refresher

        async def run() -> None:
            try:
                await refresher()
            except Exception:
                logger.warning("lender_registry_periodic_refresh_failed")

        self._bg_refresh = asyncio.ensure_future(run())

    async def ensure_ready(self) -> None:
        if self.loaded:
            self._maybe_refresh_in_background()
            return
        async with self._lock:
            if self.loaded:
                return
            try:
                if self.refresher is None:
                    raise RuntimeError("registry chưa được cấu hình")
                await self.refresher()
                self._refreshed_at = self.clock.monotonic()
            except Exception:
                logger.warning("lender_registry_load_failed")
                raise AppError(
                    ErrorCode.SERVICE_DEGRADED,
                    503,
                    "Hệ thống chưa nạp xong danh sách tổ chức cấp phép. Vui lòng thử lại sau.",
                ) from None
        if not self.loaded:
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503)

    def pipeline_for(self, sandbox: bool, *, vip: bool = False) -> ExtractionPipeline | None:
        """Chỉ phiên sandbox JUDGE_VIP dùng kho khóa VIP; PUBLIC dùng pipeline thường (EC-OPS10)."""
        return (self.vip_pipeline or self.pipeline) if (sandbox and vip) else self.pipeline


def build_pipeline(
    keys: Sequence[SecretStr], client: GeminiClient | None, settings: Settings, clock: Clock
) -> ExtractionPipeline | None:
    if not keys:
        return None
    if client is None:
        from ai_pipeline.extraction.genai_client import GoogleGenAIClient

        client = GoogleGenAIClient()
    pool = ApiKeyPool(list(keys), clock)
    return ExtractionPipeline(ModelCascade(client, pool, CascadeConfig.from_settings(settings)))


def _try[T](factory: Callable[[], T], what: str) -> T | None:
    try:
        return factory()
    except Exception:
        logger.warning("audit_services_optional_unavailable", extra={"component": what})
        return None


def build_services(
    *,
    settings: Settings,
    clock: Clock,
    redis: Redis | None = None,
    registry: LenderRegistry | None = None,
    trust: ApkTrustStore | None = None,
    devices: DeviceRepository | None = None,
    audits: AuditRepository | None = None,
    pipeline: ExtractionPipeline | None = None,
    vip_pipeline: ExtractionPipeline | None = None,
    legal: LegalExplainer | None = None,
    mule: MulePort | None = None,
    shield: ShieldHook | None = None,
    stt: SpeechToText | None = None,
    signer: Ed25519Signer | None = None,
    cpu_workers: int = 4,
) -> AuditServices:
    has_db = settings.database_url is not None
    refresher: Callable[[], Awaitable[None]] | None = None
    services_ref: list[AuditServices] = []
    if registry is None:
        if has_db:
            from app.core.db import init_engine

            engine = init_engine(settings)
            if engine is None:  # không xảy ra khi has_db; chặn để mypy/readiness rõ ràng
                raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
            pg_registry = PgLenderRegistry(engine)
            registry = pg_registry

            async def _refresh() -> None:
                # Nạp trust TRƯỚC; chỉ khi cả hai thành công mới đổi snapshot/cờ (nguyên tử).
                trust_new = await load_apk_trust(engine)
                await pg_registry.refresh()
                services_ref[0].trust = trust_new
                services_ref[0].trust_loaded = True

            refresher = _refresh
        else:
            registry = UnconfiguredRegistry()
    gemini = pipeline or build_pipeline(settings.gemini_api_keys, None, settings, clock)
    vip = vip_pipeline or build_pipeline(settings.gemini_vip_keys, None, settings, clock)
    indexer = _try(lambda: BlindIndexer.from_settings(settings), "blind_index")
    if mule is None:
        mule = NullMulePort()
        if has_db and indexer is not None:
            built = _try(lambda: _build_mule_port(indexer, settings), "mule_graph")
            if built is not None:
                mule = built
    if shield is None:
        shield = NullShieldHook()
        if redis is not None:
            shield = _build_shield_hook(redis, clock, has_db)
    services = AuditServices(
        settings=settings,
        clock=clock,
        registry=registry,
        devices=devices or (SqlDeviceRepository() if has_db else InMemoryDeviceRepository()),
        audits=audits or (SqlAuditRepository() if has_db else InMemoryAuditRepository()),
        legal=legal or (RagLegalExplainer(clock) if has_db else NullLegalExplainer()),
        mule=mule,
        shield=shield,
        cpu=CpuPool(cpu_workers),
        trust=trust or ApkTrustStore(),
        pipeline=gemini,
        vip_pipeline=vip,
        stt=stt,
        signer=signer or _try(lambda: Ed25519Signer.from_settings(settings), "ed25519"),
        indexer=indexer,
        limiter=CompositeRateLimiter.from_settings(redis, settings) if redis is not None else None,
        redis=redis,
        refresher=refresher,
        trust_loaded=refresher is None,
    )
    services_ref.append(services)
    return services


def _build_mule_port(indexer: BlindIndexer, settings: Settings) -> MulePort:
    from app.core.db import get_sessionmaker
    from app.mule_graph.repository_pg import SqlMuleGraphRepository
    from app.mule_graph.service import MuleGraphService
    from app.security.envelope import get_envelope_cipher

    repo = SqlMuleGraphRepository(get_sessionmaker())
    service = MuleGraphService(
        repo,
        indexer,
        get_envelope_cipher(settings),
        pepper_version=settings.pepper_mule_account_version,
    )
    return MuleGraphPort(service, repo, indexer)


def _build_shield_hook(redis: Redis, clock: Clock, has_db: bool) -> ShieldHook:
    from app.services.realtime.persistence import PgFallbackReader
    from app.shield_state.store import ShieldStateStore

    store = ShieldStateStore(redis, fallback=PgFallbackReader() if has_db else None)
    return StoreShieldHook(store, clock)


async def get_audit_services(
    conn: HTTPConnection, clock: Annotated[Clock, Depends(get_clock)]
) -> AuditServices:
    existing: AuditServices | None = getattr(conn.app.state, "audit_services", None)
    if existing is not None:
        return existing
    services = build_services(settings=get_settings(), clock=clock, redis=core_redis.init_redis())
    conn.app.state.audit_services = services
    return services


async def start_audit_pipeline(app: FastAPI, settings: Settings | None = None) -> None:
    """Nạp sớm registry lúc khởi động (gọi từ lifespan nếu được đấu). Lỗi → log, request đầu sẽ
    thử lại và trả 503 nếu vẫn lỗi (không bao giờ phục vụ với registry rỗng)."""
    services: AuditServices | None = getattr(app.state, "audit_services", None)
    if services is None:
        services = build_services(
            settings=settings or get_settings(),
            clock=get_clock(),
            redis=core_redis.init_redis(),
        )
        app.state.audit_services = services
    try:
        await services.ensure_ready()
    except AppError:
        logger.warning("audit_pipeline_not_ready_at_startup")


Audit = Annotated[AuditServices, Depends(get_audit_services)]

__all__ = [
    "Audit",
    "AuditServices",
    "UnconfiguredRegistry",
    "build_pipeline",
    "build_services",
    "get_audit_services",
    "start_audit_pipeline",
]
