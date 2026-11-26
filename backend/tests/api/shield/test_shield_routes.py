"""POST /shield/arm và /shield/evaluate-response (T05 + T07 nối vào route)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import httpx
from fastapi import FastAPI

from app.core.clock import FakeClock
from app.core.schemas.common import AmountBucketTier, HsmState, TriggerOrigin
from app.services.realtime.container import RealtimeServices, get_sandbox_resolver
from app.services.realtime.models import BorrowerPrincipal
from app.shield_state.fibers import LoanJourneyFiber
from app.shield_state.store import DeviceShieldSnapshot, SnapshotSource

from ._flows import arm_session
from ._helpers import DEVICE_A, signed_post

ARM = "/api/v1/shield/arm"
EVAL = "/api/v1/shield/evaluate-response"


def _arm_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "fiber_key": "default",
        "trigger_origin": "STATION_3_NOTIF",
        "amount_bucket_tier": "TIER_3M_10M",
        "client_event_id": "arm-evt-00000001",
    }
    body.update(over)
    return body


def _eval_body(session_id: uuid.UUID, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session_id": str(session_id),
        "challenge_id": "ch_abcdef0123456789",
        "utterance_text": None,
        "sensors": {"audio_call_state": "NONE"},
    }
    body.update(over)
    return body


async def test_arm_returns_s4_shield_with_24h_expiry(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    r = await signed_post(client, fake_clock, ARM, _arm_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["hsm_state"] == "S4_SHIELD_ARMED_24H"
    assert data["deduplicated"] is False
    assert data["fiber_key"] == "default"
    expires = datetime.fromisoformat(data["shield_expires_at"])
    assert (expires - fake_clock.now()).total_seconds() == 24 * 3600
    assert isinstance(data["risk_log_odds"], float)


async def test_arm_same_client_event_id_is_deduplicated(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    first = await signed_post(client, fake_clock, ARM, _arm_body())
    fake_clock.advance(1)
    second = await signed_post(client, fake_clock, ARM, _arm_body())
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    assert second.json()["session_id"] == first.json()["session_id"]


async def test_s2_ec07_sms_and_push_for_same_disbursement_arm_once(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """SMS Banking + Push app NH (cùng notif_dedupe_hash) → chỉ 1 phiên (S2-EC07)."""
    h = "a" * 64
    one = await signed_post(client, fake_clock, ARM, _arm_body(notif_dedupe_hash=h))
    two = await signed_post(
        client, fake_clock, ARM, _arm_body(notif_dedupe_hash=h, client_event_id="arm-evt-other-2")
    )
    assert one.json()["deduplicated"] is False
    assert two.status_code == 200 and two.json()["deduplicated"] is True
    assert two.json()["session_id"] == one.json()["session_id"]


async def test_arm_rejected_when_no_loan_journey_is_explicit_409_not_500(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """T05: arm_shield trả None (E3 độ tin cậy thấp, chưa có hành trình vay) → 409 tường minh."""
    r = await signed_post(
        client,
        fake_clock,
        ARM,
        _arm_body(trigger_origin="STATION_2_SCAN", client_event_id="arm-evt-no-journey"),
    )
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "CONFLICT"
    assert err["details"] == [{"field": "trigger_origin", "issue": "NO_LOAN_JOURNEY"}]
    assert "device" not in r.text.lower()
    # Không có Khiên nào được tạo: đánh giá một phiên bất kỳ → 404.
    ev = await signed_post(client, fake_clock, EVAL, _eval_body(uuid.uuid4()))
    assert ev.status_code == 404


async def test_arm_rejected_does_not_poison_idempotency_for_retry(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """Sau khi bị từ chối, cùng client_event_id vẫn thử lại được (E4 vào S4 từ S0)."""
    body = _arm_body(trigger_origin="STATION_2_SCAN", client_event_id="arm-evt-retry-0001")
    assert (await signed_post(client, fake_clock, ARM, body)).status_code == 409
    fake_clock.advance(1)
    ok = await signed_post(client, fake_clock, ARM, _arm_body(client_event_id="arm-evt-retry-0001"))
    assert ok.status_code == 200
    assert ok.json()["deduplicated"] is False


async def test_arm_requires_auth_and_rejects_unknown_fields(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    assert (await client.post(ARM, json=_arm_body())).status_code == 401
    r = await signed_post(client, fake_clock, ARM, _arm_body(amount_vnd=5_000_000))
    assert r.status_code == 422


async def test_arm_sandbox_session_uses_resolver_hook_for_t16(
    app: FastAPI,
    client: httpx.AsyncClient,
    services: RealtimeServices,
    sandbox_headers: dict[str, str],
) -> None:
    """Mặc định phiên Sandbox bị từ chối 401; T16 ghi đè ``get_sandbox_resolver`` để chấp nhận."""
    assert (await client.post(ARM, json=_arm_body(), headers=sandbox_headers)).status_code == 401
    sandbox_device = uuid.uuid4()

    async def resolver(token: str) -> BorrowerPrincipal | None:
        return BorrowerPrincipal(device_id=sandbox_device, sandbox_namespace="sbx_demo")

    app.dependency_overrides[get_sandbox_resolver] = lambda: resolver
    r = await client.post(ARM, json=_arm_body(), headers=sandbox_headers)
    assert r.status_code == 200, r.text


async def test_arm_persists_snapshot_best_effort(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    calls: list[dict[str, Any]] = []

    class Recorder:
        async def save(
            self, device_id: uuid.UUID, fibers: Sequence[LoanJourneyFiber], **kw: Any
        ) -> None:
            calls.append({"device": device_id, "fibers": fibers, **kw})

    services.shield._persistence = Recorder()
    r = await signed_post(client, fake_clock, ARM, _arm_body())
    assert r.status_code == 200
    assert calls and calls[0]["device"] == DEVICE_A
    assert calls[0]["trigger_origin"] is TriggerOrigin.STATION_3_NOTIF
    assert calls[0]["amount_bucket_tier"] is AmountBucketTier.TIER_3M_10M
    assert calls[0]["sandbox_namespace"] == "live"


async def test_evaluate_red_axis_moves_to_s7_and_recommends_escalation(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    sid = await arm_session(client, fake_clock)
    r = await signed_post(
        client,
        fake_clock,
        EVAL,
        _eval_body(sid, choice_id="ch_reversal", sensors={"audio_call_state": "ACTIVE_CELLULAR"}),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["action"] == "RED"
    assert any(a["axis"] == "C1" for a in data["detected_axes"])
    assert data["escalate_recommended"] is True
    assert data["next_hsm_state"] == HsmState.S7_ESCALATED_SCAM_CONFIRMED.value
    assert data["session_id"] == str(sid)


async def test_evaluate_pass_returns_to_safe_routine(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    sid = await arm_session(client, fake_clock)
    r = await signed_post(
        client, fake_clock, EVAL, _eval_body(sid, utterance_text="trả tiền điện tháng này")
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "PASS"
    assert r.json()["next_hsm_state"] == HsmState.S8_SAFE_ROUTINE_TX.value
    assert r.json()["follow_up_challenge"] is None


async def test_evaluate_unknown_session_404(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    await arm_session(client, fake_clock)
    r = await signed_post(client, fake_clock, EVAL, _eval_body(uuid.uuid4()))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_evaluate_degraded_state_floors_pass_to_orange(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock
) -> None:
    """Redis/PG không xác nhận được (degraded) → sàn Mức Cam, không bao giờ PASS im lặng."""

    async def degraded_load(device_id: str, now_ms: int) -> DeviceShieldSnapshot:
        return DeviceShieldSnapshot(
            device_id=device_id,
            fibers=(),
            version=0,
            state=HsmState.S4_SHIELD_ARMED_24H,
            shield_expires_at_ms=None,
            source=SnapshotSource.SAFE_DEFAULT,
            degraded=True,
        )

    services.shield._store.load = degraded_load  # type: ignore[method-assign]
    r = await signed_post(
        client, fake_clock, EVAL, _eval_body(uuid.uuid4(), utterance_text="mua gạo")
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "ORANGE_RED"
    assert r.json()["computed_scam_prob"] >= 0.35
    assert r.json()["follow_up_challenge"] is not None
