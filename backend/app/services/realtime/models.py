"""Kiểu dữ liệu dùng chung của tầng realtime (T12)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.core.schemas.family import FallbackContact, WebPushSubscription


class LinkStatus(StrEnum):
    """Khớp CHECK của ``family_guardian_links.status`` (File 4 §2.2)."""

    PENDING_CONFIRM = "PENDING_CONFIRM"
    ACTIVE = "ACTIVE"
    REVOKED_BY_BORROWER = "REVOKED_BY_BORROWER"
    REVOKED_BY_GUARDIAN = "REVOKED_BY_GUARDIAN"
    PUSH_EXPIRED_410 = "PUSH_EXPIRED_410"


@dataclass(frozen=True, slots=True)
class GuardianLink:
    """Liên kết người vay ↔ người thân. Không chứa tên thật (Zero-PII)."""

    link_id: UUID
    device_id: UUID
    guardian_alias: str
    borrower_display_label: str | None
    status: LinkStatus
    push_subscription: WebPushSubscription | None = None
    fallback_contact: FallbackContact | None = None

    def __repr__(self) -> str:  # không lộ endpoint/SĐT trong log
        return f"GuardianLink(link_id={self.link_id}, status={self.status.value})"


@dataclass(frozen=True, slots=True)
class BorrowerPrincipal:
    """Người vay đã xác thực (chữ ký thiết bị hoặc phiên Sandbox)."""

    device_id: UUID
    sandbox_namespace: str = "live"
    is_vip: bool = False  # phiên sandbox JUDGE_VIP (T16) → được dùng kho khóa Gemini VIP

    @property
    def is_sandbox(self) -> bool:
        return self.sandbox_namespace != "live"
