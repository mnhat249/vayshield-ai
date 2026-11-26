"""Chuỗi leo thang WS → Push → SMS bằng đồng hồ giả (S2-EC44, S2-EC45, EC-DB14)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx

from app.core.clock import FakeClock
from app.services.realtime.container import RealtimeServices
from app.services.realtime.models import LinkStatus
from app.services.realtime.repository import InMemoryFamilyRepository
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
from ._helpers import DEVICE_A, DEVICE_B, FakeWebSocket, ManualSleeper, signed_post, until

ESCALATE = "/api/v1/shield/escalate"


async def _setup(
    client: httpx.AsyncClient, clock: FakeClock, **pair_kwargs: object
) -> tuple[uuid.UUID, uuid.UUID, str]:
    link_id, token = await pair_guardian(client, clock, **pair_kwargs)  # type: ignore[arg-type]
    session_id = await arm_session(client, clock)
    return link_id, session_id, token


async def test_escalation_ack_within_10s_cancels_sms(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    push: FakePushSender,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
) -> None:
    """WS + Push song song; CLIENT_ALERT_ACK <= 10s → hủy SMS, người vay nhận ACKNOWLEDGED."""
    _, session_id, token = await _setup(client, fake_clock)
    g_ws, g_task = await open_socket(services, await guardian_ticket(client, token))
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))

    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert r.status_code == 202, r.text
    data = r.json()
    assert data["status"] == "PENDING_ACK"
    assert data["channels_attempted"] == ["WEBSOCKET", "WEB_PUSH"]
    assert data["guardians_targeted"] == 1

    alert = await g_ws.wait_for("EVENT_EMERGENCY_ALERT")
    assert alert["payload"]["escalation_id"] == data["escalation_id"]
    assert alert["payload"]["require_ack"] is True
    assert [e for e, _ in push.sent] == [PUSH_ENDPOINT]

    g_ws.client_send("CLIENT_ALERT_ACK", {"escalation_id": data["escalation_id"]})
    acked = await b_ws.wait_for("EVENT_ESCALATION_ACKNOWLEDGED")
    assert acked["payload"]["guardian_alias"] == "Con trai"
    assert acked["payload"]["ack_latency_ms"] >= 0

    await sleeper.advance(11)
    await services.escalation.wait_idle()
    assert sms.sent == []
    assert "EVENT_ESCALATION_FALLBACK" not in b_ws.types()
    await close_socket(g_ws, g_task)
    await close_socket(b_ws, b_task)


async def test_s2_ec44_no_ack_after_10s_sends_sms_and_notifies_borrower(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
) -> None:
    """S2-EC44: socket nửa chết/không ACK → đúng sau 10s bắn SMS và báo FALLBACK cho người vay."""
    _, session_id, token = await _setup(client, fake_clock)
    g_ws, g_task = await open_socket(services, await guardian_ticket(client, token))
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))

    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert r.status_code == 202
    await g_ws.wait_for("EVENT_EMERGENCY_ALERT")
    await until(lambda: bool(sleeper.requested))
    assert sleeper.requested == [10]

    await sleeper.advance(9)
    assert sms.sent == []  # chưa đủ 10 giây
    await sleeper.advance(1.5)
    await services.escalation.wait_idle()
    assert len(sms.sent) == 1
    assert sms.sent[0][0].value == "0901234567"
    fb = await b_ws.wait_for("EVENT_ESCALATION_FALLBACK")
    assert fb["payload"]["reason"] == "ACK_TIMEOUT"
    assert fb["payload"]["channel"] == "SMS"
    await close_socket(g_ws, g_task)
    await close_socket(b_ws, b_task)


async def test_s2_ec44_late_ack_after_timeout_still_recorded_but_sms_not_cancelled(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
) -> None:
    """ACK muộn (sau SMS) vẫn báo người vay 1 lần; không gửi SMS thứ hai."""
    _, session_id, token = await _setup(client, fake_clock)
    g_ws, g_task = await open_socket(services, await guardian_ticket(client, token))
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    esc_id = r.json()["escalation_id"]
    await until(lambda: bool(sleeper.requested))
    await sleeper.advance(11)
    await services.escalation.wait_idle()
    assert len(sms.sent) == 1
    g_ws.client_send("CLIENT_ALERT_ACK", {"escalation_id": esc_id})
    await b_ws.wait_for("EVENT_ESCALATION_ACKNOWLEDGED")
    assert len(sms.sent) == 1
    await close_socket(g_ws, g_task)
    await close_socket(b_ws, b_task)


async def test_ec_db14_push_410_marks_expired_and_goes_straight_to_sms(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    push: FakePushSender,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
    repo: InMemoryFamilyRepository,
) -> None:
    """EC-DB14: Push 410 Gone → status PUSH_EXPIRED_410, SMS NGAY (không chờ 10s)."""
    push.results[PUSH_ENDPOINT] = PushResult.GONE
    link_id, session_id, _ = await _setup(client, fake_clock)
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))

    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert r.status_code == 202
    assert r.json()["channels_attempted"] == ["WEBSOCKET", "WEB_PUSH", "SMS"]
    assert len(sms.sent) == 1  # chưa advance đồng hồ
    assert sleeper.requested == []  # không có hẹn giờ 10s cho liên kết này
    assert repo.links[link_id].status is LinkStatus.PUSH_EXPIRED_410
    assert repo.links[link_id].push_subscription is None
    fb = await b_ws.wait_for("EVENT_ESCALATION_FALLBACK")
    assert fb["payload"]["reason"] == "PUSH_EXPIRED_410"
    await close_socket(b_ws, b_task)

    # Lần mở app kế tiếp của người vay: nhắc ghép nối lại (không cần chờ gì thêm).
    fake_clock.advance(1)  # ký lại với ts mới (chống phát lại)
    b2, t2 = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    err = await b2.wait_for("SERVER_ERROR")
    assert err["payload"]["code"] == "GUARDIAN_PUSH_EXPIRED"
    await close_socket(b2, t2)


async def test_ec_db14_push_404_also_treated_as_gone(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    push: FakePushSender,
    sms: FakeSmsSender,
) -> None:
    """Sender map cả 404 và 410 thành PushResult.GONE (xem VapidPushSender); GONE → SMS ngay."""
    push.results[PUSH_ENDPOINT] = PushResult.GONE
    _, session_id, _ = await _setup(client, fake_clock)
    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert r.status_code == 202
    assert len(sms.sent) == 1


async def test_no_live_socket_and_no_push_goes_straight_to_sms(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
) -> None:
    """Người thân không có socket sống và không có Push → không chờ 10s vô ích (NO_LIVE_SOCKET)."""
    _, session_id, _ = await _setup(client, fake_clock, webpush=None)
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert r.json()["channels_attempted"] == ["WEBSOCKET", "SMS"]
    assert len(sms.sent) == 1
    fb = await b_ws.wait_for("EVENT_ESCALATION_FALLBACK")
    assert fb["payload"]["reason"] == "NO_LIVE_SOCKET"
    await close_socket(b_ws, b_task)


async def test_s2_ec45_stale_alert_over_45min_becomes_silent_digest(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    push: FakePushSender,
    sms: FakeSmsSender,
    sleeper: ManualSleeper,
) -> None:
    """S2-EC45: cảnh báo cũ > 45 phút KHÔNG Push/SMS/chuông SOS, gom thành 1 digest im lặng."""
    _, session_id, token = await _setup(client, fake_clock)
    old = fake_clock.now() - timedelta(minutes=50)
    r = await signed_post(
        client,
        fake_clock,
        ESCALATE,
        escalate_body(session_id, fake_clock, occurred_at=old.isoformat()),
    )
    assert r.status_code == 202, r.text
    assert r.json()["channels_attempted"] == []
    assert push.sent == [] and sms.sent == [] and sleeper.requested == []

    g_ws, g_task = await open_socket(services, await guardian_ticket(client, token))
    digest = await g_ws.wait_for("EVENT_ALERT_DIGEST")
    assert digest["payload"]["stale_alert_count"] == 1
    assert "EVENT_EMERGENCY_ALERT" not in g_ws.types()
    await close_socket(g_ws, g_task)

    # Bản tin tóm tắt chỉ phát 1 lần.
    g2, t2 = await open_socket(services, await guardian_ticket(client, token))
    assert "EVENT_ALERT_DIGEST" not in g2.types()
    await close_socket(g2, t2)


async def test_s2_ec45_fresh_alert_replayed_and_aging_alert_coalesced_on_reconnect(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """Người thân offline: cảnh báo ≤ 45 phút phát lại có ACK; quá 45 phút → digest."""
    _, session_id, token = await _setup(client, fake_clock, webpush=None, fallback=None)
    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert r.status_code == 202
    fake_clock.advance(10 * 60)
    g_ws, g_task = await open_socket(services, await guardian_ticket(client, token))
    assert "EVENT_EMERGENCY_ALERT" in g_ws.types()
    assert "EVENT_ALERT_DIGEST" not in g_ws.types()
    await close_socket(g_ws, g_task)

    fake_clock.advance(40 * 60)  # tổng 50 phút
    g2, t2 = await open_socket(services, await guardian_ticket(client, token))
    assert "EVENT_ALERT_DIGEST" in g2.types()
    assert "EVENT_EMERGENCY_ALERT" not in g2.types()
    await close_socket(g2, t2)


async def test_escalate_no_active_guardian(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    session_id = await arm_session(client, fake_clock)
    r = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert r.status_code == 202
    assert r.json()["status"] == "NO_ACTIVE_GUARDIAN"
    assert r.json()["guardians_targeted"] == 0


async def test_escalate_idempotent_on_client_event_id(
    client: httpx.AsyncClient,
    services: RealtimeServices,
    fake_clock: FakeClock,
    push: FakePushSender,
    sleeper: ManualSleeper,
) -> None:
    """Retry cùng client_event_id (mạng chập chờn) không phát cảnh báo/Push lần 2."""
    _, session_id, _ = await _setup(client, fake_clock)
    first = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    fake_clock.advance(2)  # ký lại với ts mới (retry hợp lệ), cùng client_event_id
    second = await signed_post(client, fake_clock, ESCALATE, escalate_body(session_id, fake_clock))
    assert first.status_code == second.status_code == 202
    assert first.json()["escalation_id"] == second.json()["escalation_id"]
    assert len(push.sent) == 1


async def test_escalate_unknown_session_404_and_other_device_cannot_ack(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    await arm_session(client, fake_clock)
    r = await signed_post(
        client, fake_clock, ESCALATE, escalate_body(uuid.uuid4(), fake_clock), DEVICE_A
    )
    assert r.status_code == 404

    # Người thân của thiết bị A không ACK được escalation của thiết bị B.
    _, token_a = await pair_guardian(client, fake_clock, device=DEVICE_A)
    link_b, token_b = await pair_guardian(client, fake_clock, device=DEVICE_B)
    session_b = await arm_session(client, fake_clock, DEVICE_B, event_id="arm-evt-0000000b")
    esc = await signed_post(
        client,
        fake_clock,
        ESCALATE,
        escalate_body(session_b, fake_clock, client_event_id="esc-evt-0000000b"),
        DEVICE_B,
    )
    esc_id = uuid.UUID(esc.json()["escalation_id"])
    link_a = (await services.pairing.resolve_session(token_a)).link
    assert await services.escalation.acknowledge(link_a, esc_id) is False
    link_b_obj = (await services.pairing.resolve_session(token_b)).link
    assert link_b_obj.link_id == link_b
    assert await services.escalation.acknowledge(link_b_obj, esc_id) is True


async def test_ws_ack_frame_forbidden_for_borrower_role(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """Người vay gửi CLIENT_ALERT_ACK → đóng 4403 ROLE_FORBIDDEN."""
    b_ws, b_task = await open_socket(services, await borrower_ticket(client, fake_clock, DEVICE_A))
    b_ws.client_send("CLIENT_ALERT_ACK", {"escalation_id": str(uuid.uuid4())})
    await b_ws.wait_for("SERVER_ERROR")
    await b_task
    assert b_ws.close_code == 4403


async def test_s2_ec44_half_open_socket_closed_with_4408(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """S2-EC44: socket không phản hồi ping qua 3 chu kỳ → đóng 4408 (hiện diện cũng hết hạn)."""
    services.ws_ping_interval_s = 0.03
    _, token = await pair_guardian(client, fake_clock)
    ws, task = await open_socket(services, await guardian_ticket(client, token))
    await ws.wait_for("SERVER_PING")
    await task
    assert ws.close_code == 4408
    assert ws.types().count("SERVER_PING") == 2
    assert ws.sent[-1]["payload"]["code"] == "HEARTBEAT_TIMEOUT"
    # hiện diện đã hết hạn theo đồng hồ giả → coi như không còn socket
    fake_clock.advance(10)
    link = (await services.pairing.resolve_session(token)).link
    from app.core.schemas.realtime import RealtimeRole

    assert await services.hub.is_live(link.device_id, RealtimeRole.GUARDIAN) is False


def test_fake_ws_type() -> None:
    assert FakeWebSocket("x").types() == []
