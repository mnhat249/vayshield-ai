"""Cổng (Protocol) tới các dịch vụ ngoài pipeline + bản Null/Fake; mọi lỗi ở đây KHÔNG được làm
hỏng phán quyết tất định (chỉ làm mất phần bổ trợ: trích dẫn luật, tín hiệu mule, đồng bộ HSM).

Evidence channel và ``scam_script_vec`` của báo cáo mule do SERVER tạo ở đây (``MuleGraphPort``),
client không có đường nào gửi các trường này (``ContractStreamMetadata`` cấm trường lạ).
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ai_pipeline.legal_rag import LegalRagService, SqlLegalRepository
from ai_pipeline.legal_rag.embedding import DIM as EMBED_DIM
from ai_pipeline.legal_rag.embedding import LexicalHashEmbedder
from app.core.clock import Clock
from app.core.logging import get_logger
from app.core.schemas.common import CitedClause, TriggerOrigin, VerdictCode
from app.core.schemas.ops import EvidenceChannel, ScamTrap, SimoDossierStatus
from app.mule_graph.repository import MuleGraphRepository
from app.mule_graph.service import MuleGraphService
from app.security.blind_index import BlindIndexer, BlindIndexKind, normalize_mule_account
from app.shield_state.fibers import EvidenceSource
from app.shield_state.hsm import HsmEvent
from app.shield_state.store import ShieldStateStore

logger = get_logger("vayshield.audit_pipeline.ports")

HARD_STOP_VERDICTS = frozenset(
    {
        VerdictCode.V_RED_ADVANCE_FEE,
        VerdictCode.V_RED_BRAND_SPOOF,
        VerdictCode.V_RED_BNPL_OR_STRAW_SCAM,
    }
)
MULE_REPORT_VERDICTS = frozenset({VerdictCode.V_RED_ADVANCE_FEE, VerdictCode.V_RED_BRAND_SPOOF})


# ------------------------------------------------------------------ legal ---


class LegalExplainer(Protocol):
    async def cited_clauses(self, verdict_code: str) -> tuple[CitedClause, ...]: ...


class NullLegalExplainer:
    async def cited_clauses(self, verdict_code: str) -> tuple[CitedClause, ...]:
        return ()


class RagLegalExplainer:
    """Trích dẫn CÓ nguyên văn đã đối chiếu từ T13 (Verdict-Pinned). Cache theo mã phán quyết để
    ``/recalculate`` không phải chờ PostgreSQL; lỗi → rỗng (không bao giờ bịa điều luật)."""

    def __init__(self, clock: Clock, ttl_s: float = 300.0) -> None:
        self._clock = clock
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, tuple[CitedClause, ...]]] = {}

    async def cited_clauses(self, verdict_code: str) -> tuple[CitedClause, ...]:
        from app.core.db import short_session

        now = time.monotonic()
        hit = self._cache.get(verdict_code)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        try:
            async with short_session() as session:
                service = LegalRagService(SqlLegalRepository(session), clock=self._clock)
                explanation = await service.explain_verdict(verdict_code)
            clauses = tuple(
                CitedClause(code=c.clause_code, title=c.title_vi) for c in explanation.citations
            )
        except Exception:
            logger.warning("legal_explain_failed")
            return hit[1] if hit is not None else ()
        self._cache[verdict_code] = (now, clauses)
        return clauses


# ------------------------------------------------------------------- mule ---


class MulePort(Protocol):
    async def is_quorum_hit(self, bank_bin: str, account_no: str) -> bool: ...

    async def report(
        self,
        *,
        bank_bin: str,
        account_no: str,
        device_id: UUID,
        script_text: str,
        impersonated_lender_id: UUID | None,
        now: datetime,
        scam_trap: ScamTrap = ScamTrap.TRAP_1_ADVANCE_FEE,
    ) -> None: ...


class NullMulePort:
    async def is_quorum_hit(self, bank_bin: str, account_no: str) -> bool:
        return False

    async def report(self, **_: object) -> None:
        return None


class MuleGraphPort:
    def __init__(
        self, service: MuleGraphService, repo: MuleGraphRepository, indexer: BlindIndexer
    ) -> None:
        self._service = service
        self._repo = repo
        self._indexer = indexer
        self._embedder = LexicalHashEmbedder()

    async def is_quorum_hit(self, bank_bin: str, account_no: str) -> bool:
        try:
            canonical = normalize_mule_account(bank_bin, account_no)
            lookup = self._indexer.lookup(BlindIndexKind.MULE_ACCOUNT, canonical)
            node = await self._repo.find_node_by_hmacs(lookup.candidates)
        except Exception:
            logger.warning("mule_lookup_failed")
            return False
        return node is not None and node.simo_dossier_status is SimoDossierStatus.QUORUM_REACHED

    async def report(
        self,
        *,
        bank_bin: str,
        account_no: str,
        device_id: UUID,
        script_text: str,
        impersonated_lender_id: UUID | None,
        now: datetime,
        scam_trap: ScamTrap = ScamTrap.TRAP_1_ADVANCE_FEE,
    ) -> None:
        try:
            vec = (await self._embedder.embed([script_text or "advance fee"]))[0]
            if len(vec) != EMBED_DIM or not any(vec):
                vec = None  # type: ignore[assignment]
            await self._service.report_mule_account(
                bank_bin=bank_bin,
                account_no=account_no,
                reporter_device_id=device_id,
                channel=EvidenceChannel.VIETQR_CRYPTO_DECODED,  # server quyết định, không từ client
                now=now,
                scam_script_vec=tuple(vec) if vec else None,
                scam_trap=scam_trap,
                impersonated_lender_id=impersonated_lender_id,
            )
        except Exception:
            logger.warning("mule_report_failed")


# ------------------------------------------------------------------ shield ---


class ShieldHook(Protocol):
    async def on_submitted(
        self, device_id: UUID, fiber_key: str, origin: TriggerOrigin, event_id: str
    ) -> None: ...

    async def on_verdict(
        self, device_id: UUID, fiber_key: str, verdict: VerdictCode, event_id: str
    ) -> None: ...

    async def on_apk(self, device_id: UUID, fiber_key: str, event_id: str) -> None: ...


class NullShieldHook:
    async def on_submitted(self, *_: object) -> None:
        return None

    async def on_verdict(self, *_: object) -> None:
        return None

    async def on_apk(self, *_: object) -> None:
        return None


class StoreShieldHook:
    """Đồng bộ HSM (File 2 §2.1): E2 khi nhận ảnh/giọng, V1 (đỏ chặn cứng) hoặc V2 khi có phán
    quyết, E1 khi APK ngoài luồng rủi ro. Best-effort: Redis hỏng không làm hỏng audit."""

    def __init__(self, store: ShieldStateStore, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    def _now_ms(self) -> int:
        return int(self._clock.now().timestamp() * 1000)

    async def _apply(
        self,
        device_id: UUID,
        fiber_key: str,
        event: HsmEvent,
        event_id: str,
        source: EvidenceSource,
    ) -> None:
        try:
            await self._store.apply_event(
                str(device_id),
                fiber_key,
                event,
                now_ms=self._now_ms(),
                event_id=event_id,
                source=source,
            )
        except Exception:
            logger.warning("shield_hook_failed", extra={"event": event.value})

    async def on_submitted(
        self, device_id: UUID, fiber_key: str, origin: TriggerOrigin, event_id: str
    ) -> None:
        source = {
            TriggerOrigin.STATION_2_VOICE: EvidenceSource.STATION_2_VOICE,
            TriggerOrigin.SANDBOX_SIM: EvidenceSource.SANDBOX_SIM,
        }.get(origin, EvidenceSource.STATION_2_SCAN)
        await self._apply(
            device_id, fiber_key, HsmEvent.E2_IMAGE_OR_VOICE_TERMS_SUBMITTED, event_id, source
        )

    async def on_verdict(
        self, device_id: UUID, fiber_key: str, verdict: VerdictCode, event_id: str
    ) -> None:
        event = (
            HsmEvent.V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT
            if verdict in HARD_STOP_VERDICTS
            else HsmEvent.V2_LOAN_TERMS_EXTRACTED
        )
        await self._apply(
            device_id, fiber_key, event, f"{event_id}:v", EvidenceSource.STATION_2_SCAN
        )

    async def on_apk(self, device_id: UUID, fiber_key: str, event_id: str) -> None:
        await self._apply(
            device_id, fiber_key, HsmEvent.E1_APK_INSTALLED, event_id, EvidenceSource.STATION_1_APK
        )


# -------------------------------------------------------------------- STT ---


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, mime_type: str, locale: str) -> str: ...
