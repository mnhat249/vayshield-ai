"""Kho liên kết người thân ``family_guardian_links`` (File 4 §2.2).

- ``FamilyRepository``: giao diện; ``InMemoryFamilyRepository`` cho dev/test;
  ``SqlFamilyRepository`` cho PostgreSQL (test ``@pytest.mark.pg``).
- Endpoint Web Push và liên hệ dự phòng được mã hóa envelope AES-256-GCM (File 4 §5.1 mục 1),
  AAD gắn với (bảng, cột, link_id). Giả định: bảng chưa có cột ``borrower_display_label`` nên nhãn
  được đóng gói cùng liên hệ dự phòng trong ``fallback_contact_cipher`` (không cần migration mới).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import text

from app.core.schemas.family import FallbackContact, WebPushSubscription
from app.security.envelope import EnvelopeCipher, get_envelope_cipher, record_aad
from app.services.realtime.models import GuardianLink, LinkStatus

# Trạng thái còn được coi là người thân đang ghép đôi (PUSH_EXPIRED_410 chỉ mất kênh Push).
LIVE_STATUSES = (LinkStatus.ACTIVE, LinkStatus.PUSH_EXPIRED_410)
TABLE = "family_guardian_links"


class FamilyRepository(Protocol):
    async def create_pending(
        self,
        *,
        link_id: UUID,
        device_id: UUID,
        guardian_alias: str,
        borrower_display_label: str | None,
        pairing_code_hash: str,
        pairing_expires_at: datetime,
    ) -> None: ...

    async def activate(
        self,
        link_id: UUID,
        *,
        push_subscription: WebPushSubscription | None,
        fallback_contact: FallbackContact | None,
        consented_at: datetime,
    ) -> GuardianLink | None:
        """PENDING_CONFIRM → ACTIVE, xóa ``pairing_code_hash``. ``None`` nếu link không còn chờ."""
        ...

    async def get_link(self, link_id: UUID) -> GuardianLink | None: ...

    async def list_live_for_device(self, device_id: UUID) -> list[GuardianLink]: ...

    async def mark_push_expired(self, link_id: UUID) -> None: ...


class InMemoryFamilyRepository:
    """Bản trong bộ nhớ (dev không có PostgreSQL; test)."""

    def __init__(self) -> None:
        self.links: dict[UUID, GuardianLink] = {}
        self.code_hashes: dict[UUID, str] = {}

    async def create_pending(
        self,
        *,
        link_id: UUID,
        device_id: UUID,
        guardian_alias: str,
        borrower_display_label: str | None,
        pairing_code_hash: str,
        pairing_expires_at: datetime,
    ) -> None:
        self.links[link_id] = GuardianLink(
            link_id=link_id,
            device_id=device_id,
            guardian_alias=guardian_alias,
            borrower_display_label=borrower_display_label,
            status=LinkStatus.PENDING_CONFIRM,
        )
        self.code_hashes[link_id] = pairing_code_hash

    async def activate(
        self,
        link_id: UUID,
        *,
        push_subscription: WebPushSubscription | None,
        fallback_contact: FallbackContact | None,
        consented_at: datetime,
    ) -> GuardianLink | None:
        cur = self.links.get(link_id)
        if cur is None or cur.status is not LinkStatus.PENDING_CONFIRM:
            return None
        new = GuardianLink(
            link_id=cur.link_id,
            device_id=cur.device_id,
            guardian_alias=cur.guardian_alias,
            borrower_display_label=cur.borrower_display_label,
            status=LinkStatus.ACTIVE,
            push_subscription=push_subscription,
            fallback_contact=fallback_contact,
        )
        self.links[link_id] = new
        self.code_hashes.pop(link_id, None)
        return new

    async def get_link(self, link_id: UUID) -> GuardianLink | None:
        return self.links.get(link_id)

    async def list_live_for_device(self, device_id: UUID) -> list[GuardianLink]:
        return [
            lk
            for lk in self.links.values()
            if lk.device_id == device_id and lk.status in LIVE_STATUSES
        ]

    async def mark_push_expired(self, link_id: UUID) -> None:
        cur = self.links.get(link_id)
        if cur is None or cur.status is not LinkStatus.ACTIVE:
            return
        self.links[link_id] = GuardianLink(
            link_id=cur.link_id,
            device_id=cur.device_id,
            guardian_alias=cur.guardian_alias,
            borrower_display_label=cur.borrower_display_label,
            status=LinkStatus.PUSH_EXPIRED_410,
            push_subscription=None,
            fallback_contact=cur.fallback_contact,
        )


class SqlFamilyRepository:
    """PostgreSQL. Mỗi thao tác mượn 1 session ngắn (EC-DB01)."""

    def __init__(self, cipher: EnvelopeCipher | None = None) -> None:
        self._cipher = cipher

    @property
    def cipher(self) -> EnvelopeCipher:
        if self._cipher is None:
            self._cipher = get_envelope_cipher()
        return self._cipher

    def _enc(self, link_id: UUID, column: str, value: dict[str, Any] | None) -> bytes | None:
        if value is None:
            return None
        raw = json.dumps(value, separators=(",", ":")).encode()
        return self.cipher.encrypt(raw, aad=record_aad(TABLE, column, link_id))

    def _dec(self, link_id: UUID, column: str, blob: bytes | None) -> dict[str, Any] | None:
        if blob is None:
            return None
        raw = self.cipher.decrypt(bytes(blob), aad=record_aad(TABLE, column, link_id))
        data: dict[str, Any] = json.loads(raw)
        return data

    def _row_to_link(self, row: Any) -> GuardianLink:
        link_id: UUID = row.id
        push = self._dec(link_id, "webpush_subscription_cipher", row.webpush_subscription_cipher)
        fb = self._dec(link_id, "fallback_contact_cipher", row.fallback_contact_cipher) or {}
        contact = fb.get("contact")
        return GuardianLink(
            link_id=link_id,
            device_id=row.borrower_device_id,
            guardian_alias=row.guardian_alias,
            borrower_display_label=fb.get("label"),
            status=LinkStatus(row.status),
            push_subscription=None if push is None else WebPushSubscription.model_validate(push),
            fallback_contact=None if contact is None else FallbackContact.model_validate(contact),
        )

    async def create_pending(
        self,
        *,
        link_id: UUID,
        device_id: UUID,
        guardian_alias: str,
        borrower_display_label: str | None,
        pairing_code_hash: str,
        pairing_expires_at: datetime,
    ) -> None:
        from app.core.db import short_session

        blob = self._enc(link_id, "fallback_contact_cipher", {"label": borrower_display_label})
        async with short_session() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO family_guardian_links (id, borrower_device_id, guardian_alias,"
                    " pairing_code_hash, pairing_expires_at, fallback_contact_cipher, status)"
                    " VALUES (:id, :dev, :alias, :h, :exp, :fb, 'PENDING_CONFIRM')"
                ),
                {
                    "id": link_id,
                    "dev": device_id,
                    "alias": guardian_alias,
                    "h": pairing_code_hash,
                    "exp": pairing_expires_at,
                    "fb": blob,
                },
            )

    async def activate(
        self,
        link_id: UUID,
        *,
        push_subscription: WebPushSubscription | None,
        fallback_contact: FallbackContact | None,
        consented_at: datetime,
    ) -> GuardianLink | None:
        from app.core.db import short_session

        async with short_session() as session, session.begin():
            cur = (
                await session.execute(
                    text(
                        "SELECT fallback_contact_cipher FROM family_guardian_links"
                        " WHERE id = :id AND status = 'PENDING_CONFIRM' FOR UPDATE"
                    ),
                    {"id": link_id},
                )
            ).first()
            if cur is None:
                return None
            prev = self._dec(link_id, "fallback_contact_cipher", cur[0]) or {}
            fb_blob = self._enc(
                link_id,
                "fallback_contact_cipher",
                {
                    "label": prev.get("label"),
                    "contact": None
                    if fallback_contact is None
                    else fallback_contact.model_dump(mode="json"),
                },
            )
            push_blob = self._enc(
                link_id,
                "webpush_subscription_cipher",
                None if push_subscription is None else push_subscription.model_dump(mode="json"),
            )
            await session.execute(
                text(
                    "UPDATE family_guardian_links SET status = 'ACTIVE', pairing_code_hash = NULL,"
                    " pairing_expires_at = NULL, consented_at = :at,"
                    " webpush_subscription_cipher = :push, fallback_contact_cipher = :fb"
                    " WHERE id = :id"
                ),
                {"id": link_id, "at": consented_at, "push": push_blob, "fb": fb_blob},
            )
        return await self.get_link(link_id)

    async def get_link(self, link_id: UUID) -> GuardianLink | None:
        from app.core.db import short_session

        async with short_session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, borrower_device_id, guardian_alias, status,"
                        " webpush_subscription_cipher, fallback_contact_cipher"
                        " FROM family_guardian_links WHERE id = :id"
                    ),
                    {"id": link_id},
                )
            ).first()
        return None if row is None else self._row_to_link(row)

    async def list_live_for_device(self, device_id: UUID) -> list[GuardianLink]:
        from app.core.db import short_session

        async with short_session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, borrower_device_id, guardian_alias, status,"
                        " webpush_subscription_cipher, fallback_contact_cipher"
                        " FROM family_guardian_links WHERE borrower_device_id = :dev"
                        " AND status IN ('ACTIVE', 'PUSH_EXPIRED_410') ORDER BY created_at"
                    ),
                    {"dev": device_id},
                )
            ).all()
        return [self._row_to_link(r) for r in rows]

    async def mark_push_expired(self, link_id: UUID) -> None:
        from app.core.db import short_session

        async with short_session() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE family_guardian_links SET status = 'PUSH_EXPIRED_410',"
                    " webpush_subscription_cipher = NULL WHERE id = :id AND status = 'ACTIVE'"
                ),
                {"id": link_id},
            )
