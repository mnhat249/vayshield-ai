"""T16 — Sandbox Mode backend + VIP quota (EC-DB16, S2-EC28, EC-OPS10, EC-OPS05)."""

from __future__ import annotations

import inspect
import json
import uuid
from typing import Any

import fakeredis
import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.schemas.common import TriggerOrigin
from app.sandbox import budget as budget_mod
from app.sandbox import deps as deps_mod
from app.sandbox import service as service_mod
from app.sandbox import session as session_mod
from app.services.realtime.container import RealtimeServices
from app.shield_state.fibers import EvidenceSource
from app.shield_state.keys import shield_state_key

from .conftest import SESSIONS, SIMULATE, VIP_CODE, Judge, RecordingPersistence

NewJudge = Any


def _use_settings(app: FastAPI, settings: Settings, **update: Any) -> None:
    app.state.sandbox_settings = settings.model_copy(update=update)


# --- EC-DB16: cô lập phiên ---
async def test_ec_db16_two_judges_same_vip_code_get_independent_namespaces(
    new_judge: NewJudge,
) -> None:
    a: Judge = await new_judge(VIP_CODE)
    b: Judge = await new_judge(VIP_CODE)
    assert a.tier == b.tier == "JUDGE_VIP"
    assert a.namespace != b.namespace
    assert a.namespace.startswith("sbx_") and b.namespace.startswith("sbx_")
    assert a.token != b.token


async def test_ec_db16_second_session_does_not_see_or_overwrite_first(
    new_judge: NewJudge,
) -> None:
    a: Judge = await new_judge(VIP_CODE)
    b: Judge = await new_judge(VIP_CODE)
    ra = await a.event("BANK_DISBURSEMENT_NOTIF", amount_bucket_tier="TIER_10M_30M")
    assert ra.status_code == 200 and ra.json()["resulting_hsm_state"] == "S4_SHIELD_ARMED_24H"
    # B mở app ngân hàng: B chưa có Khiên → không bị ảnh hưởng bởi A
    rb = await b.event("BANK_APP_OPENED")
    body = rb.json()
    assert rb.status_code == 200
    assert body["accepted"] is False
    assert body["resulting_hsm_state"] == "S0_IDLE"
    assert body["sandbox_namespace"] == b.namespace
    # B báo reset không xóa Khiên của A
    await b.event("RESET_SCENARIO")
    ra2 = await a.event("CALL_ENDED")
    assert ra2.json()["resulting_hsm_state"] == "S4_SHIELD_ARMED_24H"
    assert ra2.json()["sandbox_namespace"] == a.namespace


async def test_ec_db16_vip_code_does_not_decide_namespace_and_wrong_code_is_public(
    new_judge: NewJudge,
) -> None:
    wrong: Judge = await new_judge("sai-ma-vip")
    right: Judge = await new_judge(VIP_CODE)
    none: Judge = await new_judge(None)
    assert wrong.tier == "PUBLIC" and none.tier == "PUBLIC"
    assert right.tier == "JUDGE_VIP"
    assert len({wrong.namespace, right.namespace, none.namespace}) == 3
    assert right.data["daily_budget_remaining_usd"] == 20.0
    assert none.data["daily_budget_remaining_usd"] == 2.0


async def test_ec_db16_token_not_stored_in_clear_in_redis(
    new_judge: NewJudge, redis: fakeredis.FakeAsyncRedis
) -> None:
    j: Judge = await new_judge()
    keys = [k async for k in redis.scan_iter("vs:sbx:*")]
    assert keys and all(j.token not in k for k in keys)
    for k in keys:
        if await redis.type(k) == "hash":
            assert j.token not in (await redis.hgetall(k)).values()


async def test_session_missing_unknown_or_malformed_token_is_401(
    client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    body = {"event_type": "RESET_SCENARIO"}
    assert (await client.post(SIMULATE, json=body)).status_code == 401
    for tok in ("sbx_khong_ton_tai_0000000000", "x" * 300, "plain-token"):
        r = await client.post(SIMULATE, json=body, headers={"X-Sandbox-Session": tok})
        assert r.status_code == 401, tok
        assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_sandbox_disabled_returns_404(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    _use_settings(app, settings, sandbox_enabled=False)
    r = await client.post(SESSIONS, json={})
    assert r.status_code == 404


async def test_session_expires_at_follows_ttl(
    client: httpx.AsyncClient, services: RealtimeServices, fake_clock: FakeClock, settings: Settings
) -> None:
    r = await client.post(SESSIONS, json={})
    from datetime import datetime

    exp = datetime.fromisoformat(r.json()["expires_at"].replace("Z", "+00:00"))
    assert int((exp - fake_clock.now()).total_seconds()) == settings.sandbox_session_ttl_s


# --- S2-EC28 / lõi thật: sự kiện chạy xuyên HSM T05 + ShieldService T12 ---
async def test_s2_ec28_events_flow_through_real_hsm_to_red_and_cooling_lock(
    new_judge: NewJudge,
) -> None:
    j: Judge = await new_judge()
    d = (await j.event("BANK_DISBURSEMENT_NOTIF", amount_bucket_tier="TIER_10M_30M")).json()
    assert d["accepted"] and d["resulting_hsm_state"] == "S4_SHIELD_ARMED_24H"
    base = d["scam_probability"]
    c = (await j.event("INCOMING_SCAM_CALL", call_state="ACTIVE_VOIP", screen_share=True)).json()
    assert c["scam_probability"] > base
    o = (await j.event("BANK_APP_OPENED")).json()
    assert o["accepted"] is True
    assert "RISK_TIER_RED_HARD_INTERCEPTION" in o["side_effects"]
    assert o["scam_probability"] >= 0.75
    # S4 -E6-> S5 -G1-> S6 (khóa làm nguội) theo bảng HSM thật
    assert o["resulting_hsm_state"] == "S6_COOLING_OFF_LOCK"


async def test_s2_ec28_bank_open_without_call_is_lower_risk_than_with_call(
    new_judge: NewJudge,
) -> None:
    quiet: Judge = await new_judge()
    loud: Judge = await new_judge()
    for j in (quiet, loud):
        await j.event("BANK_DISBURSEMENT_NOTIF")
    await loud.event("INCOMING_SCAM_CALL", call_state="ACTIVE_CELLULAR")
    q = (await quiet.event("BANK_APP_OPENED")).json()
    lo = (await loud.event("BANK_APP_OPENED")).json()
    assert lo["risk_log_odds"] > q["risk_log_odds"]
    assert q["resulting_hsm_state"] == "S5_CRITICAL_INTERCEPTION"


async def test_s2_ec28_call_ended_sets_recent_call_synergy(new_judge: NewJudge) -> None:
    j: Judge = await new_judge()
    await j.event("BANK_DISBURSEMENT_NOTIF")
    base = (await j.event("RESET_SCENARIO")).json()  # reset xóa Khiên
    assert base["resulting_hsm_state"] == "S0_IDLE"
    await j.event("BANK_DISBURSEMENT_NOTIF")
    before = (await j.event("CALL_ENDED")).json()
    plain = (await (await new_judge()).event("BANK_DISBURSEMENT_NOTIF")).json()
    assert before["risk_log_odds"] > plain["risk_log_odds"]  # Ψ = 1.9 sau cúp máy ≤ 180s


async def test_s2_ec28_b2b_webhook_arms_with_sandbox_source_only(
    new_judge: NewJudge, redis: fakeredis.FakeAsyncRedis
) -> None:
    j: Judge = await new_judge()
    r = (await j.event("B2B_DISBURSEMENT_WEBHOOK")).json()
    assert r["accepted"] and r["resulting_hsm_state"] == "S4_SHIELD_ARMED_24H"
    keys = [k async for k in redis.scan_iter("vs:shield:state:*")]
    assert len(keys) == 1
    fibers = json.loads((await redis.hgetall(keys[0]))["fibers"])
    assert fibers[0]["evidence_sources"] == ["SANDBOX_SIM"]  # không có B2B_CONFIRMED


async def test_s2_ec28_user_already_transferred_goes_to_golden_hour(new_judge: NewJudge) -> None:
    j: Judge = await new_judge()
    await j.event("BANK_DISBURSEMENT_NOTIF")
    r = (await j.event("USER_ALREADY_TRANSFERRED")).json()
    assert r["accepted"] and r["resulting_hsm_state"] == "S9_GOLDEN_HOUR_TRIAGE"


async def test_s2_ec28_reset_scenario_clears_state_and_call_context(new_judge: NewJudge) -> None:
    j: Judge = await new_judge()
    await j.event("BANK_DISBURSEMENT_NOTIF")
    await j.event("INCOMING_SCAM_CALL", call_state="ACTIVE_VOIP", screen_share=True)
    r = (await j.event("RESET_SCENARIO")).json()
    assert r["resulting_hsm_state"] == "S0_IDLE"
    assert "SANDBOX_STATE_RESET" in r["side_effects"]
    again = (await j.event("BANK_DISBURSEMENT_NOTIF")).json()
    assert again["scam_probability"] < 0.75  # không còn dư cuộc gọi cũ


async def test_shield_route_accepts_sandbox_session_via_overridden_resolver(
    new_judge: NewJudge, client: httpx.AsyncClient, persistence: RecordingPersistence
) -> None:
    """T12 mặc định từ chối phiên sandbox; T16 ghi đè → /shield/arm dùng được, đúng namespace."""
    j: Judge = await new_judge()
    r = await client.post(
        "/api/v1/shield/arm",
        json={"trigger_origin": "SANDBOX_SIM", "client_event_id": "sbx-arm-evt-0001"},
        headers=j.headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["hsm_state"] == "S4_SHIELD_ARMED_24H"
    assert persistence.calls[-1]["namespace"] == j.namespace
    bad = await client.post(
        "/api/v1/shield/arm",
        json={"trigger_origin": "SANDBOX_SIM", "client_event_id": "sbx-arm-evt-0002"},
        headers={"X-Sandbox-Session": "sbx_khong_ton_tai_0000000000"},
    )
    assert bad.status_code == 401


# --- Dữ liệu sandbox không lọt vào live ---
async def test_sandbox_data_never_leaks_into_live(
    new_judge: NewJudge,
    redis: fakeredis.FakeAsyncRedis,
    persistence: RecordingPersistence,
) -> None:
    live_device = str(uuid.uuid4())
    live_key = shield_state_key(live_device)
    await redis.hset(live_key, mapping={"state": "S0_IDLE", "version": "7"})
    before = await redis.hgetall(live_key)

    j: Judge = await new_judge(VIP_CODE)
    await j.event("BANK_DISBURSEMENT_NOTIF", amount_bucket_tier="TIER_30M_100M")
    await j.event("B2B_DISBURSEMENT_WEBHOOK")
    await j.event("INCOMING_SCAM_CALL", call_state="ACTIVE_VOIP", screen_share=True)
    await j.event("BANK_APP_OPENED")
    await j.event("USER_ALREADY_TRANSFERRED")

    assert await redis.hgetall(live_key) == before  # trạng thái live nguyên vẹn
    state_keys = [k async for k in redis.scan_iter("vs:shield:state:*")]
    assert live_key in state_keys and len(state_keys) == 2
    sandbox_keys = [k for k in state_keys if k != live_key]
    assert len(sandbox_keys) == 1
    # mọi lần lưu PG đều mang namespace sandbox + origin SANDBOX_SIM, không bao giờ 'live'
    assert persistence.calls
    for c in persistence.calls:
        assert c["namespace"] == j.namespace != "live"
        assert c["namespace"].startswith("sbx_")
        assert c["origin"] is TriggerOrigin.SANDBOX_SIM
    # bằng chứng chỉ mang nguồn SANDBOX_SIM
    for call in persistence.calls:
        for f in call["fibers"]:
            assert f.evidence_sources <= {EvidenceSource.SANDBOX_SIM}


def test_sandbox_package_never_touches_live_mule_graph() -> None:
    """Phiên sandbox KHÔNG báo mule lên đồ thị live: package không import/gọi mule_graph."""
    for mod in (budget_mod, deps_mod, service_mod, session_mod):
        src = inspect.getsource(mod)
        assert "app.mule_graph" not in src.replace("``mule_graph``", ""), mod.__name__
        assert "report_mule" not in src, mod.__name__


async def test_namespace_helper_distinguishes_live() -> None:
    assert session_mod.is_sandbox_namespace("sbx_ab12cd34ef56")
    assert not session_mod.is_sandbox_namespace("live")


# --- Cầu chì ngân sách (File 6 §1.2 mục 3) ---
async def test_budget_breaker_cuts_off_public_when_daily_budget_exceeded(
    app: FastAPI,
    settings: Settings,
    new_judge: NewJudge,
    client: httpx.AsyncClient,
    fake_clock: FakeClock,
) -> None:
    # 0.003 USD = 3000 micro; mỗi sự kiện 1000 micro → 3 sự kiện, sự kiện thứ 4 bị ngắt
    _use_settings(app, settings, sandbox_daily_budget_usd=0.003)
    j: Judge = await new_judge()
    for _ in range(3):
        assert (await j.event("CALL_ENDED")).status_code == 200
    r = await j.event("CALL_ENDED")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "BUDGET_EXCEEDED"
    assert r.headers.get("retry-after") == "3600"
    # đã cạn → tạo phiên PUBLIC mới cũng bị từ chối
    r2 = await client.post(SESSIONS, json={})
    assert r2.status_code == 429 and r2.json()["error"]["code"] == "BUDGET_EXCEEDED"
    # sang ngày mới (giờ VN) → ngân sách làm mới
    fake_clock.advance(24 * 3600)
    r3 = await client.post(SESSIONS, json={})
    assert r3.status_code == 201


async def test_budget_breaker_vip_lane_is_separate_from_public(
    app: FastAPI, settings: Settings, new_judge: NewJudge
) -> None:
    _use_settings(app, settings, sandbox_daily_budget_usd=0.001)
    pub: Judge = await new_judge()
    vip: Judge = await new_judge(VIP_CODE)
    assert (await pub.event("CALL_ENDED")).status_code == 200
    assert (await pub.event("CALL_ENDED")).status_code == 429
    for _ in range(10):  # kho VIP không bị khách vãng lai làm cạn (EC-OPS10)
        assert (await vip.event("CALL_ENDED")).status_code == 200


async def test_budget_breaker_also_guards_requests_via_resolver(
    app: FastAPI, settings: Settings, new_judge: NewJudge, client: httpx.AsyncClient
) -> None:
    # 0.004 USD: tạo phiên (0) + 2 request qua resolver (0.002/lần) rồi ngắt
    _use_settings(app, settings, sandbox_daily_budget_usd=0.004)
    j: Judge = await new_judge()
    codes = []
    for i in range(3):
        r = await client.post(
            "/api/v1/shield/arm",
            json={"trigger_origin": "SANDBOX_SIM", "client_event_id": f"sbx-arm-evt-{i:04d}"},
            headers=j.headers,
        )
        codes.append(r.status_code)
    assert codes == [200, 200, 429]


# --- EC-OPS10: crawler spam ---
async def test_ec_ops10_ip_cap_blocks_public_session_flood_but_not_vip(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    _use_settings(app, settings, sandbox_max_sessions_per_ip_per_hour=3)
    for _ in range(3):
        assert (await client.post(SESSIONS, json={})).status_code == 201
    r = await client.post(SESSIONS, json={})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    # giám khảo có mã VIP (chung NAT văn phòng, EC-OPS05) vẫn vào được
    ok = await client.post(SESSIONS, json={"vip_code": VIP_CODE})
    assert ok.status_code == 201 and ok.json()["tier"] == "JUDGE_VIP"


async def test_ec_ops10_vip_code_compare_requires_exact_match(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, services: RealtimeServices
) -> None:
    _use_settings(app, settings, sandbox_vip_code=SecretStr(""))
    r = await client.post(SESSIONS, json={"vip_code": ""})
    assert r.status_code == 201 and r.json()["tier"] == "PUBLIC"
    r = await client.post(SESSIONS, json={"vip_code": VIP_CODE + "x"})
    assert r.json()["tier"] == "PUBLIC"


# --- EC-OPS05: tường lửa doanh nghiệp (chỉ HTTPS thường, không cần WS) ---
async def test_ec_ops05_simulate_event_is_plain_https_request_response(
    new_judge: NewJudge,
) -> None:
    """Một request HTTP thường trả đủ trạng thái → client rơi về polling 1.5s không mất tính
    năng (không đòi WebSocket/SSE để quan sát kết quả)."""
    j: Judge = await new_judge()
    r = await j.event("BANK_DISBURSEMENT_NOTIF")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    for field in (
        "event_id",
        "sandbox_namespace",
        "accepted",
        "resulting_hsm_state",
        "risk_log_odds",
        "scam_probability",
        "side_effects",
    ):
        assert field in body
