"""RBAC FRAUD_ANALYST + JWT Ops + EC-OPS15 (giám khảo không bị văng giữa buổi chấm)."""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from fastapi import FastAPI

from app.copilot.ops_auth import (
    JUDGE_ACCESS_TTL_S,
    JUDGE_SUBJECT,
    ROLE_FRAUD_ANALYST,
    InMemoryOpsUserDirectory,
    OpsPrincipal,
    OpsTokenService,
)
from app.core.clock import FakeClock
from app.core.errors import AppError

from .conftest import (
    ANALYST_PASSWORD,
    ANALYST_USER,
    JUDGE_CODE,
    JWT_SECRET,
    make_token_service,
)

CLUSTERS = "/api/v1/ops/mule-clusters"
TOKEN = "/api/v1/ops/auth/token"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_rbac_missing_token_is_401(ops_app: FastAPI, client: httpx.AsyncClient) -> None:
    r = await client.get(CLUSTERS)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


async def test_rbac_wrong_role_is_403_on_every_ops_data_route(
    ops_app: FastAPI, client: httpx.AsyncClient, viewer_headers: dict[str, str]
) -> None:
    for path in (
        CLUSTERS,
        "/api/v1/ops/simo-dossier?mule_node_id=019361a6-0000-7000-8000-00000000000a",
        "/api/v1/ops/stream-ticket",
    ):
        r = await client.get(path, headers=viewer_headers)
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "FORBIDDEN"


async def test_rbac_analyst_allowed(
    ops_app: FastAPI, client: httpx.AsyncClient, analyst_headers: dict[str, str]
) -> None:
    assert (await client.get(CLUSTERS, headers=analyst_headers)).status_code == 200


async def test_rbac_garbage_forged_and_foreign_tokens_are_401(
    ops_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    now = int(fake_clock.now().timestamp())
    claims = {
        "iss": "vayshield-ops",
        "sub": "x",
        "typ": "access",
        "roles": [ROLE_FRAUD_ANALYST],
        "iat": now,
        "exp": now + 600,
    }
    forged = jwt.encode(claims, "another-secret-" + "z" * 32, algorithm="HS256")
    none_alg = jwt.encode(claims, "", algorithm="none")
    wrong_issuer = jwt.encode({**claims, "iss": "evil"}, JWT_SECRET, algorithm="HS256")
    no_exp = jwt.encode(
        {k: v for k, v in claims.items() if k != "exp"}, JWT_SECRET, algorithm="HS256"
    )
    for token in ("garbage", "a.b.c", forged, none_alg, wrong_issuer, no_exp):
        r = await client.get(CLUSTERS, headers=bearer(token))
        assert r.status_code == 401, token


async def test_rbac_refresh_token_cannot_be_used_as_access(
    ops_app: FastAPI, client: httpx.AsyncClient, tokens: OpsTokenService
) -> None:
    issued = tokens.issue(OpsPrincipal(ANALYST_USER, (ROLE_FRAUD_ANALYST,)))
    r = await client.get(CLUSTERS, headers=bearer(issued.refresh_token))
    assert r.status_code == 401


async def test_access_token_expires_by_injected_clock(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    analyst_headers: dict[str, str],
    fake_clock: FakeClock,
) -> None:
    fake_clock.advance(899)
    assert (await client.get(CLUSTERS, headers=analyst_headers)).status_code == 200
    fake_clock.advance(2)
    assert (await client.get(CLUSTERS, headers=analyst_headers)).status_code == 401


async def test_password_grant_success_and_uniform_failure(
    ops_app: FastAPI, client: httpx.AsyncClient
) -> None:
    ok = await client.post(
        TOKEN,
        json={"grant_type": "password", "username": ANALYST_USER, "password": ANALYST_PASSWORD},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["roles"] == [ROLE_FRAUD_ANALYST] and body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert ok.headers["cache-control"] == "no-store"
    bad_pw = await client.post(
        TOKEN, json={"grant_type": "password", "username": ANALYST_USER, "password": "nope"}
    )
    bad_user = await client.post(
        TOKEN, json={"grant_type": "password", "username": "ghost@example.test", "password": "nope"}
    )
    missing = await client.post(TOKEN, json={"grant_type": "password"})
    assert {bad_pw.status_code, bad_user.status_code, missing.status_code} == {401}
    assert bad_pw.json()["error"]["message_vi"] == bad_user.json()["error"]["message_vi"]


async def test_ec_ops15_judge_quick_login_survives_past_15_minutes(
    ops_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """EC-OPS15: access token giám khảo dài hơn 15 phút; analyst thường thì không."""
    login = await client.post(
        TOKEN, json={"grant_type": "judge_quick_login", "judge_access_code": JUDGE_CODE}
    )
    assert login.status_code == 200
    judge = login.json()
    assert judge["expires_in"] == JUDGE_ACCESS_TTL_S > 900
    assert judge["refresh_expires_in"] == 30 * 24 * 3600
    analyst = await client.post(
        TOKEN,
        json={"grant_type": "password", "username": ANALYST_USER, "password": ANALYST_PASSWORD},
    )
    fake_clock.advance(20 * 60)
    assert (await client.get(CLUSTERS, headers=bearer(judge["access_token"]))).status_code == 200
    assert (
        await client.get(CLUSTERS, headers=bearer(analyst.json()["access_token"]))
    ).status_code == 401


async def test_ec_ops15_silent_refresh_extends_judge_session_30_days(
    ops_app: FastAPI, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    login = (
        await client.post(
            TOKEN, json={"grant_type": "judge_quick_login", "judge_access_code": JUDGE_CODE}
        )
    ).json()
    fake_clock.advance(JUDGE_ACCESS_TTL_S + 60)  # access cũ đã hết hạn
    assert (await client.get(CLUSTERS, headers=bearer(login["access_token"]))).status_code == 401
    renewed = await client.post(
        TOKEN, json={"grant_type": "refresh_token", "refresh_token": login["refresh_token"]}
    )
    assert renewed.status_code == 200
    assert (
        await client.get(CLUSTERS, headers=bearer(renewed.json()["access_token"]))
    ).status_code == 200
    fake_clock.advance(29 * 24 * 3600)  # vẫn trong 30 ngày kể từ lần gia hạn
    again = await client.post(
        TOKEN,
        json={"grant_type": "refresh_token", "refresh_token": renewed.json()["refresh_token"]},
    )
    assert again.status_code == 200


async def test_ec_ops15_wrong_or_missing_judge_code_rejected(
    ops_app: FastAPI, client: httpx.AsyncClient
) -> None:
    for payload in (
        {"grant_type": "judge_quick_login", "judge_access_code": "wrong"},
        {"grant_type": "judge_quick_login"},
    ):
        assert (await client.post(TOKEN, json=payload)).status_code == 401


def test_ec_ops15_judge_quick_login_disabled_without_code_and_refresh_revoked(
    fake_clock: FakeClock, directory: InMemoryOpsUserDirectory
) -> None:
    import asyncio

    from app.core.schemas.ops import OpsTokenRequest

    on = make_token_service(fake_clock, directory, JUDGE_CODE)
    off = make_token_service(fake_clock, directory, None)
    refresh = on.issue(
        OpsPrincipal(JUDGE_SUBJECT, (ROLE_FRAUD_ANALYST,), is_judge=True)
    ).refresh_token
    with pytest.raises(AppError):
        asyncio.run(
            off.exchange(
                OpsTokenRequest(grant_type="judge_quick_login", judge_access_code=JUDGE_CODE)
            )
        )
    with pytest.raises(AppError):  # thu hồi = tắt mã đăng nhập nhanh -> refresh cũ vô hiệu
        asyncio.run(
            off.exchange(OpsTokenRequest(grant_type="refresh_token", refresh_token=refresh))
        )


async def test_refresh_reloads_roles_from_directory_not_old_token(
    ops_app: FastAPI,
    client: httpx.AsyncClient,
    tokens: OpsTokenService,
    directory: InMemoryOpsUserDirectory,
) -> None:
    login = tokens.issue(OpsPrincipal(ANALYST_USER, (ROLE_FRAUD_ANALYST,)))
    directory.add(
        ANALYST_USER, ANALYST_PASSWORD, ("VIEWER",), salt=b"salt-analyst-test"
    )  # hạ quyền
    r = await client.post(
        TOKEN, json={"grant_type": "refresh_token", "refresh_token": login.refresh_token}
    )
    assert r.json()["roles"] == ["VIEWER"]
    ghost = tokens.issue(OpsPrincipal("deleted@example.test", (ROLE_FRAUD_ANALYST,)))
    r2 = await client.post(
        TOKEN, json={"grant_type": "refresh_token", "refresh_token": ghost.refresh_token}
    )
    assert r2.status_code == 401


async def test_access_token_cannot_be_used_as_refresh(
    ops_app: FastAPI, client: httpx.AsyncClient, tokens: OpsTokenService
) -> None:
    t = tokens.issue(OpsPrincipal(ANALYST_USER, (ROLE_FRAUD_ANALYST,)))
    r = await client.post(
        TOKEN, json={"grant_type": "refresh_token", "refresh_token": t.access_token}
    )
    assert r.status_code == 401


async def test_missing_jwt_secret_fails_closed_503(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Không cấu hình JWT_OPS_SECRET -> 503, không bao giờ chấp nhận token."""
    r = await client.get(CLUSTERS, headers=bearer("a.b.c"))
    assert r.status_code == 503
    r2 = await client.post(TOKEN, json={"grant_type": "password", "username": "a", "password": "b"})
    assert r2.status_code == 503


def test_short_secret_rejected(fake_clock: FakeClock, directory: InMemoryOpsUserDirectory) -> None:
    with pytest.raises(ValueError):
        OpsTokenService(
            secret="short",
            issuer="x",
            access_ttl_s=900,
            refresh_ttl_s=3600,
            clock=fake_clock,
            directory=directory,
            judge_code=None,
        )


def test_token_does_not_contain_password_or_code(tokens: OpsTokenService) -> None:
    t = tokens.issue(OpsPrincipal(JUDGE_SUBJECT, (ROLE_FRAUD_ANALYST,), is_judge=True))
    payload = jwt.decode(t.access_token, options={"verify_signature": False})
    assert JUDGE_CODE not in str(payload) and ANALYST_PASSWORD not in str(payload)
    assert time.time() > 0  # đồng hồ thật không ảnh hưởng hạn token (dùng FakeClock)
