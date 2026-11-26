"""Vé 1 lần (EC-DB12), HMAC webhook B2B (EC-DB19), CORS chặt (EC-DB24)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import timedelta

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from app.core.clock import FakeClock
from app.core.config import AppEnv
from app.core.errors import AppError, ErrorCode
from app.security.cors import (
    CorsConfigError,
    is_origin_allowed,
    normalize_origin,
    validated_cors_origins,
)
from app.security.partner_hmac import compute_partner_signature, verify_partner_request
from app.security.tickets import TicketNamespace, consume_ticket, issue_ticket

from ._helpers import make_settings

# ------------------------------------------------------------- EC-DB12 ---


async def test_ec_db12_ticket_single_use(redis: fakeredis.FakeAsyncRedis) -> None:
    clock = FakeClock()
    issued = await issue_ticket(
        redis, clock, namespace=TicketNamespace.WS, role="GUARDIAN", subject="link-1", ttl_s=45
    )
    assert 16 <= len(issued.ticket) <= 128
    assert issued.ttl_seconds == 45
    assert issued.expires_at == clock.now() + timedelta(seconds=45)
    claims = await consume_ticket(redis, issued.ticket, namespace=TicketNamespace.WS)
    assert (claims.role, claims.subject) == ("GUARDIAN", "link-1")
    with pytest.raises(AppError) as exc:
        await consume_ticket(redis, issued.ticket, namespace=TicketNamespace.WS)
    assert exc.value.code is ErrorCode.TICKET_INVALID_OR_CONSUMED
    assert exc.value.status_code == 401


async def test_ec_db12_reconnect_gets_new_ticket(redis: fakeredis.FakeAsyncRedis) -> None:
    """Mỗi lần reconnect xin vé mới — vé mới độc lập, vé cũ đã tiêu không ảnh hưởng."""
    clock = FakeClock()
    first = await issue_ticket(
        redis, clock, namespace=TicketNamespace.WS, role="BORROWER", subject="d", ttl_s=45
    )
    await consume_ticket(redis, first.ticket, namespace=TicketNamespace.WS)
    second = await issue_ticket(
        redis, clock, namespace=TicketNamespace.WS, role="BORROWER", subject="d", ttl_s=45
    )
    assert second.ticket != first.ticket
    assert (await consume_ticket(redis, second.ticket, namespace=TicketNamespace.WS)).subject == "d"


async def test_ec_db12_concurrent_consume_only_one_wins(redis: fakeredis.FakeAsyncRedis) -> None:
    issued = await issue_ticket(
        redis, FakeClock(), namespace=TicketNamespace.WS, role="BORROWER", subject="d", ttl_s=45
    )

    async def attempt() -> bool:
        try:
            await consume_ticket(redis, issued.ticket, namespace=TicketNamespace.WS)
        except AppError:
            return False
        return True

    results = await asyncio.gather(*(attempt() for _ in range(10)))
    assert results.count(True) == 1


async def test_ec_db12_ticket_stored_hashed_with_ttl(redis: fakeredis.FakeAsyncRedis) -> None:
    issued = await issue_ticket(
        redis, FakeClock(), namespace=TicketNamespace.WS, role="BORROWER", subject="d", ttl_s=45
    )
    keys = [k async for k in redis.scan_iter(match="vs:ws:ticket:*")]
    assert keys == ["vs:ws:ticket:" + hashlib.sha256(issued.ticket.encode()).hexdigest()]
    assert 0 < await redis.ttl(keys[0]) <= 45
    assert issued.ticket not in repr(issued)


async def test_ec_db12_namespace_isolated_and_bad_inputs(redis: fakeredis.FakeAsyncRedis) -> None:
    issued = await issue_ticket(
        redis, FakeClock(), namespace=TicketNamespace.OPS_STREAM, role="OPS", subject="u", ttl_s=45
    )
    with pytest.raises(AppError):
        await consume_ticket(redis, issued.ticket, namespace=TicketNamespace.WS)
    for bad in (None, "", "short", "x" * 129):
        with pytest.raises(AppError):
            await consume_ticket(redis, bad, namespace=TicketNamespace.WS)


# ------------------------------------------------------------- EC-DB19 ---

SECRET = b"partner-secret-test-0001"
BODY = b'{"borrower_phone_blind_hmac":"e3b0","amount_bucket_tier":"TIER_10M_30M"}'
TS = 1795543200


def test_ec_db19_partner_signature_spec_formula() -> None:
    expected = hmac.new(SECRET, f"{TS}.".encode() + BODY, hashlib.sha256).hexdigest()
    assert compute_partner_signature(SECRET, str(TS), BODY) == "sha256=" + expected


@pytest.mark.parametrize("skew", [0, 45, -45, 300, -300])
def test_ec_db19_clock_skew_within_window_accepted(skew: int) -> None:
    sig = compute_partner_signature(SECRET, str(TS), BODY)
    got = verify_partner_request(
        secret=SECRET,
        signature_header=sig,
        timestamp_header=str(TS),
        raw_body=BODY,
        now_s=TS + skew,
    )
    assert got == TS


@pytest.mark.parametrize("skew", [301, -301, 3600])
def test_ec_db19_outside_window_rejected(skew: int) -> None:
    sig = compute_partner_signature(SECRET, str(TS), BODY)
    with pytest.raises(AppError) as exc:
        verify_partner_request(
            secret=SECRET,
            signature_header=sig,
            timestamp_header=str(TS),
            raw_body=BODY,
            now_s=TS + skew,
        )
    assert exc.value.code is ErrorCode.TIMESTAMP_OUT_OF_WINDOW


def test_ec_db19_bad_signature_and_uppercase_hex() -> None:
    sig = compute_partner_signature(SECRET, str(TS), BODY)
    ok = verify_partner_request(
        secret=SECRET,
        signature_header="sha256=" + sig[7:].upper(),
        timestamp_header=str(TS),
        raw_body=BODY,
        now_s=TS,
    )
    assert ok == TS
    cases = [
        (sig, str(TS), BODY + b" "),  # body bị sửa
        (sig, str(TS + 1), BODY),  # timestamp bị sửa (không đổi được chữ ký)
        ("sha256=" + "0" * 64, str(TS), BODY),
        ("md5=abc", str(TS), BODY),
        (sig, "not-a-number", BODY),
    ]
    for header, ts, body in cases:
        with pytest.raises(AppError) as exc:
            verify_partner_request(
                secret=SECRET, signature_header=header, timestamp_header=ts, raw_body=body, now_s=TS
            )
        assert exc.value.status_code == 401
    with pytest.raises(AppError):
        verify_partner_request(
            secret=SECRET, signature_header=None, timestamp_header=str(TS), raw_body=BODY, now_s=TS
        )


# ------------------------------------------------------------- EC-DB24 ---


def test_ec_db24_origin_validation() -> None:
    assert normalize_origin("HTTPS://App.VayShield.vn/") == "https://app.vayshield.vn"
    for bad in ("*", "https://*.vayshield.vn", "ftp://x.vn", "https://x.vn/path", "null", ""):
        with pytest.raises(CorsConfigError):
            normalize_origin(bad)


def test_ec_db24_production_requires_https_exact_origins() -> None:
    cfg = make_settings(
        app_env=AppEnv.PRODUCTION,
        cors_origins=["http://app.vayshield.vn"],
        database_url=SecretStr("postgresql+asyncpg://u:p@db/x"),
        redis_url=SecretStr("redis://r:6379/0"),
        jwt_ops_secret=SecretStr("jwt-test-secret-" + "z" * 32),
    )
    with pytest.raises(CorsConfigError):
        validated_cors_origins(cfg)
    ok = make_settings(cors_origins=["https://app.vayshield.vn", "http://localhost:3000"])
    assert validated_cors_origins(ok) == ["https://app.vayshield.vn", "http://localhost:3000"]


def test_ec_db24_is_origin_allowed_exact_match() -> None:
    allowed = ["https://app.vayshield.vn"]
    assert is_origin_allowed("https://app.vayshield.vn", allowed)
    assert is_origin_allowed(None, allowed)  # app native / server-to-server
    for evil in (
        "https://app.vayshield.vn.evil.com",
        "https://evilapp.vayshield.vn",
        "http://app.vayshield.vn",
        "null",
        "https://app.vayshield.vn:8443",
    ):
        assert not is_origin_allowed(evil, allowed)


async def test_ec_db24_app_cors_blocks_foreign_origin(client: httpx.AsyncClient) -> None:
    """App thật: origin lạ không nhận ACAO; origin hợp lệ được echo chính xác, không credentials."""
    pre = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-vayshield-device-signature",
    }
    evil = await client.options(
        "/api/v1/devices/register", headers={"Origin": "https://evil.example", **pre}
    )
    assert "access-control-allow-origin" not in evil.headers
    good = await client.options(
        "/api/v1/devices/register", headers={"Origin": "http://localhost:3000", **pre}
    )
    assert good.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert good.headers.get("access-control-allow-credentials") != "true"
    simple = await client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers
