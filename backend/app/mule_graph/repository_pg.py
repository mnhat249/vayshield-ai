"""Bản PostgreSQL (SQLAlchemy async) của ``B2BRepository`` và ``MuleGraphRepository``.

Theo ``migrations/0001_core_schema.sql``:

- ``fintech_partners`` tra theo ``api_key_prefix`` = 16 ký tự đầu của ``X-VayShield-Partner-Key``
  (giả định: cột VARCHAR(16) UNIQUE; khóa đối tác phải khác nhau trong 16 ký tự đầu). Bí mật HMAC
  nằm ở ``hmac_secret_cipher`` (envelope, AAD ``fintech_partners|hmac_secret_cipher|<id>``).
- ``b2b_disbursement_shields`` có RLS: mọi truy vấn chạy trong giao dịch có
  ``set_config('app.current_partner_id', <partner>, true)`` (EC-DB15). Bảng không có cột hạn Khiên
  và thiết bị khớp → hạn = ``created_at + shield_duration_hours``; ``matched_device_id`` không lưu.
- Đỉnh/cạnh mule: cạnh chèn ``ON CONFLICT DO NOTHING``; quorum đếm lại bằng ``COUNT(*)`` trong cùng
  giao dịch SAU khi khóa đỉnh (``FOR UPDATE``) để câu lệnh đếm lấy snapshot mới (EC-DB07).
  ``lock_scope`` = ``pg_advisory_xact_lock`` theo dải VA/STK: tuần tự hóa các báo cáo cùng dải,
  không deadlock giữa các STK cùng dải.
- ``is_dynamic_va_range`` không phải cột: suy ra từ ``va_prefix_hmac`` và ``cluster_id`` đều có.
"""

from __future__ import annotations

import contextvars
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import set_local_setting
from app.core.errors import AppError, ErrorCode
from app.core.logging import get_logger
from app.core.schemas.b2b import B2BShieldStatus
from app.core.schemas.ops import EvidenceChannel, ScamTrap, SeverityLevel, SimoDossierStatus
from app.mule_graph.models import (
    ClusterRecord,
    DeviceMatch,
    MuleEdgeRecord,
    MuleNodeRecord,
    PartnerRecord,
    ShieldRecord,
)
from app.security.envelope import EnvelopeCipher, EnvelopeDecryptionError, record_aad

_log = get_logger("vayshield.mule_graph.pg")

API_KEY_PREFIX_LEN: Final = 16
PARTNER_CACHE_TTL_S: Final = 120.0
_PARTNER_KEY_RE: Final = re.compile(r"[A-Za-z0-9_\-]{16,128}")
PARTNERS_TABLE: Final = "fintech_partners"
SECRET_COLUMN: Final = "hmac_secret_cipher"  # noqa: S105 - tên cột, không phải bí mật

_NODE_COLS: Final = (
    "id, bank_bin, account_hmac_sha256, va_prefix_hmac, pepper_version, masked_account_display,"
    " account_cipher_envelope, distinct_device_quorum, threat_confidence, simo_dossier_status,"
    " cluster_id, first_reported_at, last_reported_at"
)

_NODE_SELECT: Final = "SELECT " + _NODE_COLS + " FROM mule_account_nodes"  # noqa: S608
_SQL_NODE_BY_HMAC: Final = _NODE_SELECT + " WHERE account_hmac_sha256 = ANY(:c) LIMIT 1"
_SQL_NODE_BY_ID: Final = _NODE_SELECT + " WHERE id = :i"
_SQL_NODES_BY_VA: Final = (
    _NODE_SELECT + " WHERE bank_bin = :b AND va_prefix_hmac = ANY(:v) ORDER BY id"
)
_SQL_NODES_IN_CLUSTER: Final = _NODE_SELECT + " WHERE cluster_id = :c"


def _vec_to_text(vec: Sequence[float] | None) -> str | None:
    return None if vec is None else "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _vec_from_text(raw: str | None) -> tuple[float, ...] | None:
    if raw is None:
        return None
    body = raw.strip().strip("[]")
    return tuple(float(x) for x in body.split(",")) if body else ()


def _node_from_row(row: Any) -> MuleNodeRecord:
    return MuleNodeRecord(
        id=row.id,
        bank_bin=row.bank_bin,
        account_hmac=row.account_hmac_sha256,
        va_prefix_hmac=row.va_prefix_hmac,
        pepper_version=row.pepper_version,
        masked_account_display=row.masked_account_display,
        account_cipher_envelope=bytes(row.account_cipher_envelope),
        distinct_device_quorum=row.distinct_device_quorum,
        threat_confidence=row.threat_confidence,
        simo_dossier_status=SimoDossierStatus(row.simo_dossier_status),
        cluster_id=row.cluster_id,
        is_dynamic_va_range=row.va_prefix_hmac is not None and row.cluster_id is not None,
        first_reported_at=row.first_reported_at,
        last_reported_at=row.last_reported_at,
    )


class SqlB2BRepository:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        cipher: EnvelopeCipher,
        *,
        secret_cache_ttl_s: float = PARTNER_CACHE_TTL_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sm = sessionmaker
        self._cipher = cipher
        self._ttl = secret_cache_ttl_s
        self._monotonic = monotonic
        self._cache: dict[str, tuple[float, PartnerRecord]] = {}

    async def get_partner_by_key(self, partner_key: str) -> PartnerRecord | None:
        """Giả định: DB chỉ lưu 16 ký tự đầu (``api_key_prefix``) nên không so khớp được toàn bộ
        khóa; kiểm dạng/độ dài chuẩn rồi để chữ ký HMAC (bí mật riêng) làm chốt xác thực. Nên thêm
        cột băm toàn khóa bằng migration MỚI (ngoài vùng owns T14).

        Bí mật đã giải mã được cache ngắn (``secret_cache_ttl_s``) để không giải envelope + truy vấn
        mỗi request; đối tác bị vô hiệu hóa có hiệu lực chậm nhất sau TTL này."""
        if not _PARTNER_KEY_RE.fullmatch(partner_key):
            return None
        prefix = partner_key[:API_KEY_PREFIX_LEN]
        hit = self._cache.get(prefix)
        if hit is not None and hit[0] > self._monotonic():
            return hit[1]
        partner = await self._load_partner(prefix)
        if partner is None:
            self._cache.pop(prefix, None)
        elif self._ttl > 0:
            self._cache[prefix] = (self._monotonic() + self._ttl, partner)
        return partner

    async def _load_partner(self, prefix: str) -> PartnerRecord | None:
        async with self._sm() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT id, hmac_secret_cipher, rate_limit_rpm, is_active,"
                        " lender_registry_id FROM fintech_partners WHERE api_key_prefix = :p"
                    ),
                    {"p": prefix},
                )
            ).first()
        if row is None:
            return None
        try:
            secret = self._cipher.decrypt(
                bytes(row.hmac_secret_cipher),
                aad=record_aad(PARTNERS_TABLE, SECRET_COLUMN, row.id),
            )
        except EnvelopeDecryptionError:
            _log.error("b2b_partner_secret_undecryptable")
            raise AppError(ErrorCode.SERVICE_DEGRADED, 503) from None
        return PartnerRecord(
            id=row.id,
            hmac_secret=secret,
            rate_limit_rpm=row.rate_limit_rpm,
            is_active=row.is_active,
            lender_registry_id=row.lender_registry_id,
        )

    async def find_device_by_phone_hmacs(self, candidates: Sequence[str]) -> DeviceMatch | None:
        async with self._sm() as session, session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT id, phone_optin_exp_at FROM anonymous_devices"
                        " WHERE phone_blind_hmac = ANY(:c) LIMIT 1"
                    ),
                    {"c": list(candidates)},
                )
            ).first()
        return None if row is None else DeviceMatch(row.id, row.phone_optin_exp_at)

    @staticmethod
    def _shield_from_row(row: Any) -> ShieldRecord:
        return ShieldRecord(
            id=row.id,
            partner_id=row.partner_id,
            idempotency_key=row.idempotency_key,
            borrower_phone_hmac=row.borrower_phone_hmac,
            amount_bucket_tier=row.amount_bucket_tier,
            shield_duration_hours=row.shield_duration_hours,
            status=B2BShieldStatus(row.status),
            shield_expires_at=row.created_at + timedelta(hours=row.shield_duration_hours),
            created_at=row.created_at,
        )

    async def create_shield(self, shield: ShieldRecord) -> tuple[ShieldRecord, bool]:
        async with self._sm() as session, session.begin():
            await set_local_setting(session, "app.current_partner_id", str(shield.partner_id))
            inserted = (
                await session.execute(
                    text(
                        "INSERT INTO b2b_disbursement_shields (id, partner_id, idempotency_key,"
                        " borrower_phone_hmac, amount_bucket_tier, shield_duration_hours, status,"
                        " created_at) VALUES (:id, :p, :k, :h, :t, :d, :s, :c)"
                        " ON CONFLICT (partner_id, idempotency_key) DO NOTHING"
                        " RETURNING id, partner_id, idempotency_key, borrower_phone_hmac,"
                        " amount_bucket_tier, shield_duration_hours, status, created_at"
                    ),
                    {
                        "id": shield.id,
                        "p": shield.partner_id,
                        "k": shield.idempotency_key,
                        "h": shield.borrower_phone_hmac,
                        "t": shield.amount_bucket_tier,
                        "d": shield.shield_duration_hours,
                        "s": shield.status.value,
                        "c": shield.created_at,
                    },
                )
            ).first()
            if inserted is not None:
                return self._shield_from_row(inserted), True
            existing = await self._select_shield(session, shield.partner_id, shield.idempotency_key)
        if existing is None:  # RLS ẩn hàng / bị xóa giữa chừng — không bao giờ coi là thành công
            raise AppError(ErrorCode.CONFLICT, 409)
        return existing, False

    @staticmethod
    async def _select_shield(
        session: AsyncSession, partner_id: UUID, key: str
    ) -> ShieldRecord | None:
        row = (
            await session.execute(
                text(
                    "SELECT id, partner_id, idempotency_key, borrower_phone_hmac,"
                    " amount_bucket_tier, shield_duration_hours, status, created_at"
                    " FROM b2b_disbursement_shields"
                    " WHERE partner_id = :p AND idempotency_key = :k"
                ),
                {"p": partner_id, "k": key},
            )
        ).first()
        return None if row is None else SqlB2BRepository._shield_from_row(row)

    async def get_shield(self, partner_id: UUID, idempotency_key: str) -> ShieldRecord | None:
        async with self._sm() as session, session.begin():
            await set_local_setting(session, "app.current_partner_id", str(partner_id))
            return await self._select_shield(session, partner_id, idempotency_key)

    async def count_active_waves(self, lender_registry_id: UUID | None, since: datetime) -> int:
        if lender_registry_id is None:
            return 0
        async with self._sm() as session, session.begin():
            value = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM fraud_campaign_clusters"
                        " WHERE impersonated_lender_id = :l AND severity_level = 'CRITICAL_WAVE'"
                        " AND last_seen_at >= :s"
                    ),
                    {"l": lender_registry_id, "s": since},
                )
            ).scalar_one()
        return int(value)


_TXN: contextvars.ContextVar[AsyncSession | None] = contextvars.ContextVar(
    "mule_graph_txn", default=None
)


class SqlMuleGraphRepository:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._sm() as session, session.begin():
            token = _TXN.set(session)
            try:
                yield
            finally:
                _TXN.reset(token)

    @asynccontextmanager
    async def _use(self) -> AsyncIterator[AsyncSession]:
        current = _TXN.get()
        if current is not None:
            yield current
            return
        async with self._sm() as session, session.begin():
            yield session

    async def lock_scope(self, key: str) -> None:
        async with self._use() as s:
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": key}
            )

    async def find_node_by_hmacs(self, candidates: Sequence[str]) -> MuleNodeRecord | None:
        async with self._use() as s:
            row = (
                await s.execute(
                    text(_SQL_NODE_BY_HMAC),
                    {"c": list(candidates)},
                )
            ).first()
        return None if row is None else _node_from_row(row)

    async def get_node(self, node_id: UUID) -> MuleNodeRecord | None:
        async with self._use() as s:
            row = (
                await s.execute(
                    text(_SQL_NODE_BY_ID),
                    {"i": node_id},
                )
            ).first()
        return None if row is None else _node_from_row(row)

    async def create_node(self, node: MuleNodeRecord) -> MuleNodeRecord:
        async with self._use() as s:
            await s.execute(
                text(
                    "INSERT INTO mule_account_nodes (id, bank_bin, account_hmac_sha256,"
                    " va_prefix_hmac, pepper_version, masked_account_display,"
                    " account_cipher_envelope, distinct_device_quorum, threat_confidence,"
                    " simo_dossier_status, first_reported_at, last_reported_at)"
                    " VALUES (:id, :bin, :h, :va, :pv, :m, :cipher, :q, :c, :st, :f, :l)"
                    " ON CONFLICT (account_hmac_sha256) DO NOTHING"
                ),
                {
                    "id": node.id,
                    "bin": node.bank_bin,
                    "h": node.account_hmac,
                    "va": node.va_prefix_hmac,
                    "pv": node.pepper_version,
                    "m": node.masked_account_display,
                    "cipher": node.account_cipher_envelope,
                    "q": node.distinct_device_quorum,
                    "c": node.threat_confidence,
                    "st": node.simo_dossier_status.value,
                    "f": node.first_reported_at,
                    "l": node.last_reported_at,
                },
            )
        found = await self.find_node_by_hmacs([node.account_hmac])
        if found is None:
            raise RuntimeError("không đọc lại được mule node vừa chèn")
        return found

    async def save_node(self, node: MuleNodeRecord) -> None:
        async with self._use() as s:
            await s.execute(
                text(
                    "UPDATE mule_account_nodes SET account_hmac_sha256 = :h, va_prefix_hmac = :va,"
                    " pepper_version = :pv, distinct_device_quorum = :q, threat_confidence = :c,"
                    " simo_dossier_status = :st, cluster_id = :cl, last_reported_at = :l"
                    " WHERE id = :id"
                ),
                {
                    "id": node.id,
                    "h": node.account_hmac,
                    "va": node.va_prefix_hmac,
                    "pv": node.pepper_version,
                    "q": node.distinct_device_quorum,
                    "c": node.threat_confidence,
                    "st": node.simo_dossier_status.value,
                    "cl": node.cluster_id,
                    "l": node.last_reported_at,
                },
            )

    async def add_edge(self, edge: MuleEdgeRecord) -> bool:
        async with self._use() as s:
            res = await s.execute(
                text(
                    "INSERT INTO mule_incident_edges (mule_node_id, reporter_device_id,"
                    " evidence_channel, scam_script_vec, created_at)"
                    " VALUES (:n, :d, :ch, CAST(CAST(:v AS text) AS vector), :t)"
                    " ON CONFLICT (mule_node_id, reporter_device_id) DO NOTHING"
                    " RETURNING id"
                ),
                {
                    "n": edge.mule_node_id,
                    "d": edge.reporter_device_id,
                    "ch": edge.evidence_channel.value,
                    "v": _vec_to_text(edge.scam_script_vec),
                    "t": edge.created_at,
                },
            )
            return res.first() is not None

    async def refresh_quorum(self, node_id: UUID) -> int:
        async with self._use() as s:
            # Khóa đỉnh rồi mới đếm: câu lệnh UPDATE sau là statement mới → thấy cạnh đã commit
            # của giao dịch trước (READ COMMITTED), không ghi đè quorum thấp hơn (EC-DB07).
            await s.execute(
                text("SELECT id FROM mule_account_nodes WHERE id = :i FOR UPDATE"), {"i": node_id}
            )
            value = (
                await s.execute(
                    text(
                        "UPDATE mule_account_nodes SET distinct_device_quorum ="
                        " (SELECT COUNT(*) FROM mule_incident_edges WHERE mule_node_id = :i)"
                        " WHERE id = :i RETURNING distinct_device_quorum"
                    ),
                    {"i": node_id},
                )
            ).scalar_one()
        return int(value)

    async def edges_for_nodes(self, node_ids: Sequence[UUID]) -> list[MuleEdgeRecord]:
        if not node_ids:
            return []
        async with self._use() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT mule_node_id, reporter_device_id, evidence_channel, created_at,"
                        " scam_script_vec::text AS vec FROM mule_incident_edges"
                        " WHERE mule_node_id = ANY(:ids)"
                    ),
                    {"ids": list(node_ids)},
                )
            ).all()
        return [
            MuleEdgeRecord(
                mule_node_id=r.mule_node_id,
                reporter_device_id=r.reporter_device_id,
                evidence_channel=EvidenceChannel(r.evidence_channel),
                created_at=r.created_at,
                scam_script_vec=_vec_from_text(r.vec),
            )
            for r in rows
        ]

    async def find_nodes_by_va(
        self, bank_bin: str, va_prefix_hmacs: Sequence[str]
    ) -> list[MuleNodeRecord]:
        async with self._use() as s:
            rows = (
                await s.execute(
                    text(_SQL_NODES_BY_VA),
                    {"b": bank_bin, "v": list(va_prefix_hmacs)},
                )
            ).all()
        return [_node_from_row(r) for r in rows]

    @staticmethod
    def _cluster_from_row(row: Any) -> ClusterRecord:
        return ClusterRecord(
            id=row.id,
            campaign_label=row.campaign_label,
            dominant_scam_trap=ScamTrap(row.dominant_scam_trap),
            severity_level=SeverityLevel(row.severity_level),
            first_seen_at=row.first_seen_at,
            last_seen_at=row.last_seen_at,
            incident_count_24h=row.incident_count_24h,
            centroid_embedding=_vec_from_text(row.centroid),
            impersonated_lender_id=row.impersonated_lender_id,
        )

    async def get_cluster(self, cluster_id: UUID) -> ClusterRecord | None:
        async with self._use() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id, campaign_label, dominant_scam_trap, severity_level,"
                        " first_seen_at, last_seen_at, incident_count_24h,"
                        " centroid_embedding::text AS centroid, impersonated_lender_id"
                        " FROM fraud_campaign_clusters WHERE id = :i"
                    ),
                    {"i": cluster_id},
                )
            ).first()
        return None if row is None else self._cluster_from_row(row)

    async def save_cluster(self, cluster: ClusterRecord) -> None:
        async with self._use() as s:
            await s.execute(
                text(
                    "INSERT INTO fraud_campaign_clusters (id, campaign_label, dominant_scam_trap,"
                    " severity_level, first_seen_at, last_seen_at, incident_count_24h,"
                    " centroid_embedding, impersonated_lender_id)"
                    " VALUES (:id, :lbl, :trap, :sev, :f, :l, :n,"
                    " CAST(CAST(:v AS text) AS vector), :lender)"
                    " ON CONFLICT (id) DO UPDATE SET severity_level = EXCLUDED.severity_level,"
                    " last_seen_at = EXCLUDED.last_seen_at,"
                    " incident_count_24h = EXCLUDED.incident_count_24h,"
                    " centroid_embedding = EXCLUDED.centroid_embedding,"
                    " impersonated_lender_id = EXCLUDED.impersonated_lender_id"
                ),
                {
                    "id": cluster.id,
                    "lbl": cluster.campaign_label,
                    "trap": cluster.dominant_scam_trap.value,
                    "sev": cluster.severity_level.value,
                    "f": cluster.first_seen_at,
                    "l": cluster.last_seen_at,
                    "n": cluster.incident_count_24h,
                    "v": _vec_to_text(cluster.centroid_embedding),
                    "lender": cluster.impersonated_lender_id,
                },
            )

    async def nodes_in_cluster(self, cluster_id: UUID) -> list[MuleNodeRecord]:
        async with self._use() as s:
            rows = (
                await s.execute(
                    text(_SQL_NODES_IN_CLUSTER),
                    {"c": cluster_id},
                )
            ).all()
        return [_node_from_row(r) for r in rows]
