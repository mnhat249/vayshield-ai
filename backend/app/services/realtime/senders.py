"""Cổng gửi Web Push (VAPID) và SMS/Email dự phòng — interface + fake (D6: không gọi mạng thật).

- ``VapidPushSender`` dùng ``pywebpush`` (chạy trong threadpool). 404/410 → ``PushResult.GONE``
  (EC-DB14). Chỉ dựng khi có ``VAPID_PRIVATE_KEY``.
- SMS/Email: chưa chọn nhà cung cấp thật (File 4 chỉ nêu "SMS / Email Fallback Gateway") nên mặc
  định ``UnconfiguredSmsSender`` trả ``False`` + log lỗi — KHÔNG im lặng coi như đã gửi.
- Endpoint Web Push chỉ nhận từ dịch vụ push đã biết (chống SSRF khi server gọi URL do client nạp).
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Final, Protocol
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.schemas.family import FallbackContact, WebPushSubscription
from app.core.schemas.realtime import PushPayload

logger = get_logger("vayshield.realtime.senders")

ALLOWED_PUSH_HOST_SUFFIXES: Final = (
    "fcm.googleapis.com",
    "push.apple.com",
    "push.services.mozilla.com",
    "notify.windows.com",
    "push.api.chrome.google.com",
)
PUSH_TTL_S: Final = 60


class PushResult(StrEnum):
    DELIVERED = "DELIVERED"
    GONE = "GONE"  # 404/410: subscription chết (EC-DB14)
    FAILED = "FAILED"  # lỗi tạm thời/khác


class PushSender(Protocol):
    async def send(self, subscription: WebPushSubscription, payload: PushPayload) -> PushResult: ...


class SmsSender(Protocol):
    async def send(self, contact: FallbackContact, message_vi: str) -> bool: ...


def is_allowed_push_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.port not in (None, 443) or parts.username:
        return False
    return any(host == s or host.endswith("." + s) for s in ALLOWED_PUSH_HOST_SUFFIXES)


class FakePushSender:
    """Ghi lại các lần gửi; ``results`` quy định kết quả theo endpoint (mặc định DELIVERED)."""

    def __init__(self, results: dict[str, PushResult] | None = None) -> None:
        self.results = results or {}
        self.sent: list[tuple[str, PushPayload]] = []

    async def send(self, subscription: WebPushSubscription, payload: PushPayload) -> PushResult:
        self.sent.append((subscription.endpoint, payload))
        return self.results.get(subscription.endpoint, PushResult.DELIVERED)


class FakeSmsSender:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[FallbackContact, str]] = []

    async def send(self, contact: FallbackContact, message_vi: str) -> bool:
        self.sent.append((contact, message_vi))
        return self.ok


class UnconfiguredPushSender:
    async def send(self, subscription: WebPushSubscription, payload: PushPayload) -> PushResult:
        logger.error("push_not_configured: thiếu VAPID_PRIVATE_KEY, không thể gửi Web Push")
        return PushResult.FAILED


class UnconfiguredSmsSender:
    async def send(self, contact: FallbackContact, message_vi: str) -> bool:
        logger.error("sms_provider_not_configured: không gửi được SMS/Email dự phòng")
        return False


class VapidPushSender:
    def __init__(self, private_key: str, subject: str) -> None:
        self._private_key = private_key
        self._subject = subject

    def _send_sync(self, subscription: WebPushSubscription, payload: PushPayload) -> PushResult:
        from pywebpush import WebPushException, webpush

        try:
            webpush(
                subscription_info=subscription.model_dump(mode="json"),
                data=payload.model_dump_json(),
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
                ttl=PUSH_TTL_S,
                headers={"Urgency": "high"},
                timeout=5,
            )
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            return PushResult.GONE if status in (404, 410) else PushResult.FAILED
        except Exception:
            return PushResult.FAILED
        return PushResult.DELIVERED

    async def send(self, subscription: WebPushSubscription, payload: PushPayload) -> PushResult:
        if not is_allowed_push_endpoint(subscription.endpoint):
            return PushResult.FAILED
        return await asyncio.to_thread(self._send_sync, subscription, payload)


def build_push_sender(settings: Settings) -> PushSender:
    if settings.vapid_private_key is None:
        return UnconfiguredPushSender()
    return VapidPushSender(settings.vapid_private_key.get_secret_value(), settings.vapid_subject)


def build_sms_sender(settings: Settings) -> SmsSender:
    # "fake" chỉ dành cho dev/test; production phải cấu hình nhà cung cấp thật (quyết định mở).
    if settings.sms_provider == "fake" and not settings.is_production:
        return FakeSmsSender()
    return UnconfiguredSmsSender()
