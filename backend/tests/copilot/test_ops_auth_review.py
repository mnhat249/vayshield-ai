"""Review T15: refresh dùng-một-lần, thu hồi khi đổi mã judge, judge không unseal STK thật,
rate limit riêng cho /auth/token, lệch đồng hồ giữa node, vé SSE kiểm vai trò."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import UUID

import httpx
import jwt
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from redis.asyncio import Redis

import app.copilot.ops_auth as ops_auth
from app.copilot.feed import MAX_STREAM_S, stream_limit_s
from app.copilot.ops_auth import (
    IAT_LEEWAY_S,
    JUDGE_SUBJECT,
    REFRESH_GRACE_S,
    ROLE_FRAUD_ANALYST,
    InMemoryOpsUserDirectory,
    OpsPrincipal,
    OpsTokenService,
    RedisRefreshStateStore,
)
from app.copilot.repository import InMemoryOpsRepository
from app.core.clock import FakeClock
from app.core.config import AppEnv, get_settings
from app.core.errors import AppError
from app.core.schemas.ops import OpsTokenRequest, ScamTrap, SeverityLevel, SimoDossierStatus
from app.mule_graph.models import ClusterRecord, MuleNodeRecord
from app.security.tickets import TicketNamespace, issue_ticket
from seeds import data as seed_data

from .conftest import (
    ACCOUNT_A,
    ANALYST_USER,
    JUDGE_CODE,
    JWT_SECRET,
    Seeded,
    make_token_service,
)

TOKEN = "/api/v1/ops/auth/token"
DOSSIER = "/api/v1/ops/simo-dossier"


def _refresh(t: str) -> OpsTokenRequest:
    return OpsTokenRequest(grant_type="refresh_token", refresh_token=t)


def _judge_login() -> OpsTokenRequest:
    return OpsTokenRequest(grant_type="judge_quick_login", judge_access_code=JUDGE_CODE)


async def test_ec_ops15_review_major3_refresh_token_is_single_use_after_grace(
    tokens: OpsTokenService, fake_clock: FakeClock
) -> None:
    first = await tokens.exchange(_judge_login())
    second = await tokens.exchange(_refresh(first.refresh_token))
    fake_clock.advance(REFRESH_GRACE_S + 1)  # ngoài cửa sổ ân hạn
    with pytest.raises(AppError) as err:
        await tokens.exchange(_refresh(first.refresh_token))  # dùng lại r1
    assert err.value.status_code == 401
    # Dùng lại token cũ = nghi đánh cắp: cả họ (kể cả r2 còn hợp lệ) bị thu hồi...
    with pytest.raises(AppError):
        await tokens.exchange(_refresh(second.refresh_token))
    # ...và access token còn hạn của họ đó cũng chết (kể cả access 4 giờ của giám khảo).
    with pytest.raises(AppError):
        await tokens.verify_access_active(second.access_token)


async def test_ec_ops15_review_round2_parallel_refresh_gets_same_pair_not_logged_out(
    tokens: OpsTokenService,
) -> None:
    """Hai tab refresh song song: cùng 1 cặp token, KHÔNG thu hồi họ."""
    first = await tokens.exchange(_judge_login())
    req = _refresh(first.refresh_token)
    a, b = await asyncio.gather(tokens.exchange(req), tokens.exchange(req))
    assert (a.access_token, a.refresh_token) == (b.access_token, b.refresh_token)
    again = await tokens.exchange(_refresh(a.refresh_token))  # họ vẫn sống
    assert tokens.verify_access(again.access_token).is_judge
    assert (await tokens.verify_access_active(a.access_token)).is_judge


async def test_ec_ops15_review_round2_redis_parallel_refresh_gets_same_pair(
    fake_clock: FakeClock, directory: InMemoryOpsUserDirectory, redis: Redis
) -> None:
    svc = OpsTokenService(
        secret=JWT_SECRET,
        issuer="vayshield-ops",
        access_ttl_s=900,
        refresh_ttl_s=3600,
        clock=fake_clock,
        directory=directory,
        judge_code=JUDGE_CODE,
        state=RedisRefreshStateStore(redis),
    )
    first = await svc.exchange(_judge_login())
    req = _refresh(first.refresh_token)
    a, b = await asyncio.gather(svc.exchange(req), svc.exchange(req))
    assert a.refresh_token == b.refresh_token
    jti = jwt.decode(first.refresh_token, options={"verify_signature": False})["jti"]
    await redis.delete(f"vs:ops:rt:grace:{jti}")  # hết cửa sổ ân hạn
    with pytest.raises(AppError):
        await svc.exchange(req)
    with pytest.raises(AppError):  # họ bị thu hồi: cả access còn hạn cũng bị từ chối
        await svc.verify_access_active(a.access_token)


async def test_ec_ops15_review_round2_revoked_family_access_rejected_over_http(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    tokens: OpsTokenService,
    fake_clock: FakeClock,
) -> None:
    t = await tokens.exchange(_judge_login())
    h = {"Authorization": f"Bearer {t.access_token}"}
    assert (await client.get("/api/v1/ops/mule-clusters", headers=h)).status_code == 200
    renewed = await tokens.exchange(_refresh(t.refresh_token))
    fake_clock.advance(REFRESH_GRACE_S + 1)
    with pytest.raises(AppError):
        await tokens.exchange(_refresh(t.refresh_token))  # reuse -> thu hồi họ
    assert (await client.get("/api/v1/ops/mule-clusters", headers=h)).status_code == 401
    h2 = {"Authorization": f"Bearer {renewed.access_token}"}
    assert (await client.get("/api/v1/ops/mule-clusters", headers=h2)).status_code == 401


async def test_ec_ops15_review_major3_rotation_chain_works_when_used_once_each(
    tokens: OpsTokenService,
) -> None:
    t = await tokens.exchange(_judge_login())
    for _ in range(3):
        t = await tokens.exchange(_refresh(t.refresh_token))
    assert tokens.verify_access(t.access_token).is_judge


async def test_ec_ops15_review_major3_changing_judge_code_kills_old_tokens(
    fake_clock: FakeClock, directory: InMemoryOpsUserDirectory
) -> None:
    old = make_token_service(fake_clock, directory, judge_code=JUDGE_CODE)
    t = await old.exchange(_judge_login())
    rotated = make_token_service(fake_clock, directory, judge_code="NEW-rotated-code-123456")
    with pytest.raises(AppError):
        await rotated.exchange(_refresh(t.refresh_token))
    with pytest.raises(AppError):  # access cũ cũng chết
        rotated.verify_access(t.access_token)


async def test_ec_ops15_review_major3_refresh_without_jti_rejected(
    tokens: OpsTokenService, fake_clock: FakeClock
) -> None:
    now = int(fake_clock.now().timestamp())
    legacy = jwt.encode(
        {
            "iss": "vayshield-ops",
            "sub": ANALYST_USER,
            "typ": "refresh",
            "roles": [ROLE_FRAUD_ANALYST],
            "jdg": False,
            "iat": now,
            "exp": now + 3600,
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(AppError):
        await tokens.exchange(_refresh(legacy))


async def test_ec_ops15_review_major3_redis_failure_is_fail_closed_503(
    fake_clock: FakeClock, directory: InMemoryOpsUserDirectory
) -> None:
    class Broken:
        async def set(self, *a: object, **k: object) -> bool:
            raise ConnectionError("down")

        async def exists(self, *a: object, **k: object) -> int:
            raise ConnectionError("down")

    svc = OpsTokenService(
        secret=JWT_SECRET,
        issuer="vayshield-ops",
        access_ttl_s=900,
        refresh_ttl_s=3600,
        clock=fake_clock,
        directory=directory,
        judge_code=JUDGE_CODE,
        state=RedisRefreshStateStore(Broken()),  # type: ignore[arg-type]
    )
    t = svc.issue(OpsPrincipal(JUDGE_SUBJECT, (ROLE_FRAUD_ANALYST,), is_judge=True))
    with pytest.raises(AppError) as err:
        await svc.exchange(_refresh(t.refresh_token))
    assert err.value.status_code == 503


async def test_ec_ops15_review_major3_redis_store_single_use(redis: Redis) -> None:
    store = RedisRefreshStateStore(redis)
    assert await store.consume("j1", 60, 10) is True
    assert await store.consume("j1", 60, 10) is False
    assert await store.grace_result("j1") == "pending"
    await store.remember("j1", "{}", 10)
    assert await store.grace_result("j1") == "{}"
    assert not await store.is_family_revoked("s1")
    await store.revoke_family("s1", 60)
    assert await store.is_family_revoked("s1")


async def test_ec_ops15_review_minor_clock_skew_between_nodes_tolerated(
    fake_clock: FakeClock, directory: InMemoryOpsUserDirectory
) -> None:
    ahead = FakeClock(fake_clock.now() + timedelta(seconds=IAT_LEEWAY_S - 5))
    way_ahead = FakeClock(fake_clock.now() + timedelta(seconds=IAT_LEEWAY_S + 60))
    p = OpsPrincipal("a@example.test", (ROLE_FRAUD_ANALYST,))
    node_b = make_token_service(fake_clock, directory)
    ok = make_token_service(ahead, directory).issue(p).access_token
    assert node_b.verify_access(ok).subject == "a@example.test"
    bad = make_token_service(way_ahead, directory).issue(p).access_token
    with pytest.raises(AppError):
        node_b.verify_access(bad)


# --- Major 4: judge không unseal STK thật ------------------------------------------------------


async def _judge_headers(tokens: OpsTokenService) -> tuple[dict[str, str], str]:
    t = await tokens.exchange(_judge_login())
    return {"Authorization": f"Bearer {t.access_token}"}, t.access_token


async def test_ec_ops16_review_major4_judge_cannot_unseal_real_account(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    tokens: OpsTokenService,
    seeded: Seeded,
    ops_repo: InMemoryOpsRepository,
) -> None:
    headers, _ = await _judge_headers(tokens)
    r = await client.get(
        DOSSIER, params={"mule_node_id": str(seeded.node_a), "unseal": "true"}, headers=headers
    )
    assert r.status_code == 403
    assert ACCOUNT_A not in r.text and not ops_repo.audit_entries
    plain = await client.get(DOSSIER, params={"mule_node_id": str(seeded.node_a)}, headers=headers)
    assert plain.status_code == 200 and plain.json()["unsealed_account_number"] is None


def _load_real_seed(ops_repo: InMemoryOpsRepository, now: datetime) -> list[UUID]:
    """Nạp ĐÚNG dữ liệu seed thật từ ``seeds.data`` (phong bì giả như seeds/run.py), không gắn
    lại nhãn."""
    ids: list[UUID] = []
    for cl in seed_data.CLUSTERS:
        ops_repo.graph.clusters[cl.cluster_id] = ClusterRecord(
            id=cl.cluster_id,
            campaign_label=cl.label,
            dominant_scam_trap=ScamTrap(cl.trap),
            severity_level=SeverityLevel(cl.severity),
            first_seen_at=now,
            last_seen_at=now,
            incident_count_24h=cl.incidents_24h,
        )
        for a in cl.accounts:
            ops_repo.graph.nodes[a.node_id] = MuleNodeRecord(
                id=a.node_id,
                bank_bin="SANDBOX",
                account_hmac=a.hmac,
                va_prefix_hmac=None,
                pepper_version=1,
                masked_account_display=a.masked_display,
                account_cipher_envelope=b"sandbox_seed:no-real-account",
                distinct_device_quorum=a.quorum,
                threat_confidence=a.confidence,
                simo_dossier_status=SimoDossierStatus(seed_data.dossier_status(a.quorum)),
                cluster_id=cl.cluster_id,
            )
            ids.append(a.node_id)
    return ids


async def test_ec_ops16_review_round2_judge_unseals_real_seed_data_with_simulated_account(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    tokens: OpsTokenService,
    ops_repo: InMemoryOpsRepository,
    fake_clock: FakeClock,
) -> None:
    """Dữ liệu seed THẬT (phong bì không giải mã được): không 500, trả STK mô phỏng; mỗi giám khảo
    một actor audit riêng."""
    ids = _load_real_seed(ops_repo, fake_clock.now())
    h1, a1 = await _judge_headers(tokens)
    h2, a2 = await _judge_headers(tokens)
    for h in (h1, h2):
        r = await client.get(
            DOSSIER, params={"mule_node_id": str(ids[0]), "unseal": "true"}, headers=h
        )
        assert r.status_code == 200, r.text
        assert r.json()["unsealed_account_number"].startswith("SIMULATED-")
    actors = [e[0] for e in ops_repo.audit_entries]
    assert len(set(actors)) == 2  # hai giám khảo không dùng chung 1 actor
    assert all(a.startswith(JUDGE_SUBJECT + "#") for a in actors)
    assert tokens.verify_access(a1).session_id != tokens.verify_access(a2).session_id


async def test_ec_ops16_review_round2_seed_label_on_cluster_does_not_unlock_real_node(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    tokens: OpsTokenService,
    seeded: Seeded,
    ops_repo: InMemoryOpsRepository,
) -> None:
    """Nhãn mô phỏng gắn lên cụm chứa node THẬT không mở khóa unseal cho judge (xét theo node)."""
    ops_repo.graph.clusters[seeded.cluster_id].campaign_label = "[Dữ liệu Mô phỏng Thực tiễn] x"
    headers, _ = await _judge_headers(tokens)
    r = await client.get(
        DOSSIER, params={"mule_node_id": str(seeded.node_a), "unseal": "true"}, headers=headers
    )
    assert r.status_code == 403 and ACCOUNT_A not in r.text


async def test_ec_ops16_review_round2_analyst_unseal_of_seed_node_is_also_simulated(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    ops_repo: InMemoryOpsRepository,
    fake_clock: FakeClock,
) -> None:
    ids = _load_real_seed(ops_repo, fake_clock.now())
    r = await client.get(
        DOSSIER, params={"mule_node_id": str(ids[1]), "unseal": "true"}, headers=analyst_headers
    )
    assert r.status_code == 200 and r.json()["unsealed_account_number"].startswith("SIMULATED-")


# --- Minor: rate limit riêng, mã judge yếu, vé SSE ---------------------------------------------


async def test_ec_ops15_review_minor_auth_token_has_own_10_per_minute_ip_bucket(
    ops_app: FastAPI, client: httpx.AsyncClient
) -> None:
    body = {"grant_type": "judge_quick_login", "judge_access_code": "wrong"}
    codes = [(await client.post(TOKEN, json=body)).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10
    assert codes[10:] == [429, 429]


def test_ec_ops15_review_minor_weak_judge_code_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Cfg:
        app_env = AppEnv.PRODUCTION
        jwt_ops_secret = SecretStr(JWT_SECRET)
        judge_ops_quick_login_code = SecretStr("short-code")
        jwt_ops_issuer = "vayshield-ops"
        jwt_ops_access_ttl_s = 900
        jwt_ops_refresh_ttl_s = 3600

    monkeypatch.setattr(ops_auth, "get_settings", lambda: Cfg())
    clock, users = FakeClock(), InMemoryOpsUserDirectory()
    weak = ops_auth.get_ops_token_service(clock, users, None)  # type: ignore[arg-type]
    with pytest.raises(AppError):
        weak._judge_principal("short-code")
    Cfg.judge_ops_quick_login_code = SecretStr("a-long-enough-judge-code")
    strong = ops_auth.get_ops_token_service(clock, users, None)  # type: ignore[arg-type]
    assert strong._judge_principal("a-long-enough-judge-code").is_judge


async def test_ec_ops15_review_minor_live_feed_rejects_ticket_without_analyst_role(
    ops_app: FastAPI, client: httpx.AsyncClient, redis: Redis, fake_clock: FakeClock
) -> None:
    issued = await issue_ticket(
        redis,
        fake_clock,
        namespace=TicketNamespace.OPS_STREAM,
        role="VIEWER",
        subject="v",
        ttl_s=45,
    )
    r = await client.get(f"/api/v1/ops/live-feed?ticket={issued.ticket}")
    assert r.status_code == 403


def test_ec_ops15_review_minor_stream_limit_not_above_access_ttl() -> None:
    assert stream_limit_s() <= get_settings().jwt_ops_access_ttl_s
    assert stream_limit_s() <= MAX_STREAM_S


async def test_ec_ops15_review_round2_username_bucket_is_separate_from_other_users(
    ops_app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes.ops as ops_routes

    monkeypatch.setattr(ops_routes, "AUTH_IP_RPM", 1000)
    bad = {"grant_type": "password", "username": "victim@example.test", "password": "x"}
    codes = [(await client.post(TOKEN, json=bad)).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    other = {"grant_type": "password", "username": ANALYST_USER, "password": "x"}
    assert (await client.post(TOKEN, json=other)).status_code == 401  # không bị 429 lây


async def test_ec_ops15_review_round2_global_cap_counts_only_failures(
    ops_app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.routes.ops as ops_routes

    monkeypatch.setattr(ops_routes, "AUTH_IP_RPM", 1000)
    monkeypatch.setattr(ops_routes, "AUTH_SCOPE_RPM", 1000)
    monkeypatch.setattr(ops_routes, "AUTH_GLOBAL_FAIL_PER_MIN", 3)
    good = {"grant_type": "judge_quick_login", "judge_access_code": JUDGE_CODE}
    for _ in range(6):  # đăng nhập ĐÚNG không làm cạn trần toàn cục
        assert (await client.post(TOKEN, json=good)).status_code == 200
    bad = {"grant_type": "judge_quick_login", "judge_access_code": "wrong"}
    assert [(await client.post(TOKEN, json=bad)).status_code for _ in range(3)] == [401] * 3
    assert (await client.post(TOKEN, json=bad)).status_code == 429  # trần theo THẤT BẠI
