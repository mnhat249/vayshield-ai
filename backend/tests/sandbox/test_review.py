"""T16 review round: phân quyền route, dò mã VIP, thứ tự limiter/ngân sách, kho khóa VIP."""

from __future__ import annotations

import uuid
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from app.core.clock import FakeClock
from app.core.config import Settings
from app.sandbox.session import hash_token
from app.services.realtime.container import RealtimeServices

from .conftest import SESSIONS, VIP_CODE, Judge

NewJudge = Any


def _use_settings(app: FastAPI, settings: Settings, **update: Any) -> None:
    app.state.sandbox_settings = settings.model_copy(update=update)


def _escalate_body(services: RealtimeServices) -> dict[str, Any]:
    return {
        "session_id": str(uuid.uuid4()),
        "reason": "USER_CONFIRMED_SCAM",
        "hsm_state": "S4_SHIELD_ARMED_24H",
        "client_event_id": "abcdefgh123",
        "occurred_at": services.clock.now().isoformat(),
    }


async def test_sandbox_session_cannot_reach_family_or_escalate_and_never_sends_sms_push(
    new_judge: NewJudge, client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    """Phiên sandbox ẩn danh không gửi được SMS/Push thật, không tạo family link (BLOCKER)."""
    j: Judge = await new_judge()
    r = await client.post(
        "/api/v1/family/pair-init", json={"guardian_alias": "x"}, headers=j.headers
    )
    assert r.status_code == 401
    for ev in ("BANK_DISBURSEMENT_NOTIF", "B2B_DISBURSEMENT_WEBHOOK", "USER_ALREADY_TRANSFERRED"):
        assert (await j.event(ev)).status_code == 200
    r = await client.post(
        "/api/v1/shield/escalate", headers=j.headers, json=_escalate_body(services)
    )
    assert r.status_code == 401
    r = await client.get("/api/v1/ws-ticket", headers=j.headers)
    assert r.status_code == 401
    assert services.escalation._sms.sent == []  # type: ignore[attr-defined]
    assert services.escalation._push.sent == []  # type: ignore[attr-defined]


async def test_shield_service_rejects_sandbox_principal_on_escalate_defense_in_depth(
    services: RealtimeServices,
) -> None:
    from app.core.errors import AppError
    from app.core.schemas.shield import EscalateRequest
    from app.services.realtime.models import BorrowerPrincipal

    req = EscalateRequest.model_validate(_escalate_body(services))
    who = BorrowerPrincipal(device_id=uuid.uuid4(), sandbox_namespace="sbx_x")
    with pytest.raises(AppError) as exc:
        await services.shield.escalate(who, req)
    assert exc.value.status_code == 403
    assert services.escalation._sms.sent == []  # type: ignore[attr-defined]


async def test_ec_ops10_wrong_vip_guesses_are_capped_regardless_of_correctness(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    _use_settings(app, settings, sandbox_max_sessions_per_ip_per_hour=5)
    codes = [
        (await client.post(SESSIONS, json={"vip_code": f"doan-{i}"})).status_code for i in range(8)
    ]
    assert codes[:5] == [201] * 5 and codes[5:] == [429] * 3
    ok = await client.post(SESSIONS, json={"vip_code": VIP_CODE})
    assert ok.status_code == 429  # mã đúng cũng bị chặn sau khi vượt trần dò mã


async def test_ec_ops10_rate_limited_events_do_not_spend_shared_budget(
    new_judge: NewJudge, redis: fakeredis.FakeAsyncRedis
) -> None:
    j: Judge = await new_judge()
    other: Judge = await new_judge()
    codes = [(await j.event("CALL_ENDED")).status_code for _ in range(200)]
    assert codes.count(200) == 30  # nhịp phiên 30/phút; phần còn lại 429 mà không trừ kho
    keys = [k async for k in redis.scan_iter("vs:sbx:budget:PUBLIC:*")]
    assert int(await redis.get(keys[0]) or 0) == 30 * 1_000
    assert (await other.event("CALL_ENDED")).status_code == 200


async def _burn(j: Judge, fake_clock: FakeClock, max_minutes: int = 60) -> int:
    ok = 0
    for _ in range(max_minutes):
        for _ in range(30):
            r = await j.event("CALL_ENDED")
            if r.status_code == 200:
                ok += 1
            elif r.json()["error"]["code"] == "BUDGET_EXCEEDED":
                return ok
        fake_clock.advance(61)
    return ok


async def test_ec_ops10_per_session_quota_stops_one_session_burning_shared_pool(
    new_judge: NewJudge, fake_clock: FakeClock
) -> None:
    j: Judge = await new_judge()
    other: Judge = await new_judge()
    assert await _burn(j, fake_clock) == 200  # 0.2 USD / 0.001
    assert (await other.event("CALL_ENDED")).status_code == 200


async def test_per_ip_daily_quota_caps_public_sessions_from_one_ip(
    new_judge: NewJudge, fake_clock: FakeClock
) -> None:
    total = 0
    for _ in range(4):
        total += await _burn(await new_judge(), fake_clock)
    assert total == 500  # 0.5 USD / IP / ngày dù mỗi phiên được 0.2


async def test_ec_ops10_public_session_does_not_use_vip_gemini_pool() -> None:
    from app.services.audit_pipeline.container import AuditServices

    pub, vip = object(), object()
    svc = AuditServices.__new__(AuditServices)
    svc.pipeline = pub  # type: ignore[assignment]
    svc.vip_pipeline = vip  # type: ignore[assignment]
    assert svc.pipeline_for(True) is pub
    assert svc.pipeline_for(True, vip=True) is vip
    assert svc.pipeline_for(False, vip=True) is pub


async def test_principal_vip_flag_follows_tier() -> None:
    from app.core.schemas.sandbox import SandboxTier
    from app.sandbox.session import SandboxSession

    for tier, expected in ((SandboxTier.PUBLIC, False), (SandboxTier.JUDGE_VIP, True)):
        s = SandboxSession(
            token_hash="h",
            namespace="sbx_a",
            device_id=uuid.uuid4(),
            tier=tier,
            expires_at=__import__("datetime").datetime.now(),
        )
        assert s.principal().is_vip is expected


async def test_expired_session_by_clock_is_rejected(
    new_judge: NewJudge, fake_clock: FakeClock, settings: Settings
) -> None:
    j: Judge = await new_judge()
    fake_clock.advance(settings.sandbox_session_ttl_s + 1)
    assert (await j.event("CALL_ENDED")).status_code == 401


async def test_reset_scenario_clears_idem_and_smurf_keys(
    new_judge: NewJudge, redis: fakeredis.FakeAsyncRedis
) -> None:
    j: Judge = await new_judge()
    await j.event("BANK_DISBURSEMENT_NOTIF")
    await j.event("BANK_APP_OPENED")
    assert [k async for k in redis.scan_iter("vs:smurf:win:*")]
    await j.event("RESET_SCENARIO")
    for pat in ("vs:smurf:win:*", "vs:idem:arm:*", "vs:shield:state:*", "vs:sbx:ctx:*"):
        assert [k async for k in redis.scan_iter(pat)] == [], pat
    assert await redis.exists(f"vs:sbx:sess:{hash_token(j.token)}")


async def test_odd_events_on_missing_fiber_do_not_crash(new_judge: NewJudge) -> None:
    j: Judge = await new_judge()
    for et in ("BANK_APP_OPENED", "B2B_DISBURSEMENT_WEBHOOK", "RESET_SCENARIO"):
        assert (await j.event(et, fiber_key="nope")).status_code == 200
