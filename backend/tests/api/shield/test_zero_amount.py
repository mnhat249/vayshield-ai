"""Zero-Amount Privacy Envelope: payload tới người thân không có số tiền/tên công ty vay."""

from __future__ import annotations

import json
import re

import httpx

from app.core.clock import FakeClock
from app.services.realtime.container import RealtimeServices
from app.services.realtime.senders import FakePushSender, FakeSmsSender, PushResult

from ._flows import (
    PUSH_ENDPOINT,
    arm_session,
    borrower_ticket,
    close_socket,
    escalate_body,
    guardian_ticket,
    open_socket,
    pair_guardian,
)
from ._helpers import DEVICE_A, ManualSleeper, signed_post, until

AMOUNT_RE = re.compile(
    r"\d[\d.,]*\s*(?:đ\b|vnd|vnđ|triệu|nghìn|ngàn|tr\b|k\b)|\d{1,3}(?:[.,]\d{3})+|\d{5,}",
    re.IGNORECASE,
)
LENDERS = ("tima", "home credit", "fe credit", "mcredit", "f88", "vamo", "doctor đồng", "momo")


_ID_NOISE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # UUID
    r"|\d{4}-\d{2}-\d{2}T[\d:.+-]+"  # timestamp ISO
    r"|\"(?:heartbeat_interval_s|ack_timeout_s|server_ts_ms)\": \d+",
    re.IGNORECASE,
)


def _assert_clean(label: str, blob: str) -> None:
    blob = _ID_NOISE.sub("", blob)  # bỏ UUID/timestamp/tham số kỹ thuật, chỉ xét nội dung
    assert not AMOUNT_RE.search(blob), f"{label} lộ số tiền: {blob}"
    low = blob.lower()
    for name in LENDERS:
        assert name not in low, f"{label} lộ tên công ty vay: {name}"


async def test_escalate_request_rejects_amount_and_lender_fields(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """Zero-Amount: client KHÔNG thể gửi số tiền/tên công ty (extra=forbid → 422)."""
    await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    for extra in ({"amount_vnd": 5_000_000}, {"lender_name": "Tima"}, {"account_number": "123"}):
        r = await signed_post(
            client,
            fake_clock,
            "/api/v1/shield/escalate",
            escalate_body(sid, fake_clock, **extra),
        )
        assert r.status_code == 422, extra
        fake_clock.advance(1)


async def test_alert_frames_push_and_sms_carry_no_amount_or_lender(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    push: FakePushSender,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
) -> None:
    """Phiên được bật với bậc số tiền + nguồn thông báo; mọi đầu ra tới người thân vẫn sạch."""
    _, token = await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)  # amount_bucket_tier = TIER_3M_10M ở phía người vay
    g_ws, g_task = await open_socket(services, await guardian_ticket(client, token))
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))

    for reason in ("COERCION_DETECTED", "USER_ALREADY_TRANSFERRED"):
        fake_clock.advance(1)
        r = await signed_post(
            client,
            fake_clock,
            "/api/v1/shield/escalate",
            escalate_body(sid, fake_clock, reason=reason, client_event_id=f"zero-amt-{reason}"),
        )
        assert r.status_code == 202, r.text
    await until(lambda: g_ws.types().count("EVENT_EMERGENCY_ALERT") == 2)
    await until(lambda: len(sleeper.requested) == 2)
    await sleeper.advance(11)
    await services.escalation.wait_idle()

    for frame in g_ws.sent + b_ws.sent:
        _assert_clean(frame["type"], json.dumps(frame, ensure_ascii=False))
    assert len(push.sent) == 2
    for endpoint, payload in push.sent:
        assert endpoint == PUSH_ENDPOINT
        _assert_clean("push", payload.model_dump_json())
    assert sms.sent
    for _, text in sms.sent:
        _assert_clean("sms", text)
    await close_socket(g_ws, g_task)
    await close_socket(b_ws, b_task)


async def test_push_410_path_also_zero_amount(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    push: FakePushSender,
    sms: FakeSmsSender,
) -> None:
    push.results[PUSH_ENDPOINT] = PushResult.GONE
    await pair_guardian(client, fake_clock)
    sid = await arm_session(client, fake_clock)
    r = await signed_post(
        client, fake_clock, "/api/v1/shield/escalate", escalate_body(sid, fake_clock)
    )
    assert r.status_code == 202
    for _, text in sms.sent:
        _assert_clean("sms", text)
