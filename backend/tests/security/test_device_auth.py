"""Dependency xác thực chữ ký ECDSA P-256 thiết bị: sai chữ ký → 401 (DoD T10)."""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import fakeredis
import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI

from app.core.clock import FakeClock, get_clock
from app.core.errors import install_error_handlers
from app.security.device_auth import (
    VerifiedDevice,
    VerifiedDeviceCaller,
    _to_der,
    canonical_device_message,
    get_device_key_resolver,
    parse_device_signature_header,
    replay_key,
)

NOW = datetime(2026, 11, 25, 1, 0, tzinfo=UTC)
TS = int(NOW.timestamp())
DEVICE_ID = uuid.UUID("019361a0-0000-7000-8000-000000000001")
PRIV = ec.derive_private_key(123456789, ec.SECP256R1())
PUB_POINT = PRIV.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_header(
    method: str,
    path: str,
    body: bytes,
    *,
    ts: int = TS,
    key: ec.EllipticCurvePrivateKey = PRIV,
    device_id: uuid.UUID = DEVICE_ID,
    raw_p1363: bool = False,
) -> str:
    der = key.sign(canonical_device_message(method, path, ts, body), ec.ECDSA(hashes.SHA256()))
    sig = der
    if raw_p1363:
        r, s = decode_dss_signature(der)
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"v1;device_id={device_id};ts={ts};sig={_b64url(sig)}"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
async def api(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/t/echo")
    async def echo(device: VerifiedDeviceCaller) -> dict[str, str]:
        return {"device_id": str(device.device_id)}

    keys = {DEVICE_ID: PUB_POINT}

    async def resolver(device_id: uuid.UUID) -> bytes | None:
        return keys.get(device_id)

    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_device_key_resolver] = lambda: resolver
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


BODY = b'{"x":1}'
H = "X-VayShield-Device-Signature"


async def test_valid_signature_passes(api: httpx.AsyncClient) -> None:
    r = await api.post(
        "/t/echo?a=1", content=BODY, headers={H: sign_header("POST", "/t/echo?a=1", BODY)}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"device_id": str(DEVICE_ID)}


async def test_valid_webcrypto_p1363_signature_passes(api: httpx.AsyncClient) -> None:
    hdr = sign_header("POST", "/t/echo", BODY, raw_p1363=True)
    r = await api.post("/t/echo", content=BODY, headers={H: hdr})
    assert r.status_code == 200, r.text


async def test_wrong_key_signature_401(api: httpx.AsyncClient) -> None:
    other = ec.generate_private_key(ec.SECP256R1())
    r = await api.post(
        "/t/echo", content=BODY, headers={H: sign_header("POST", "/t/echo", BODY, key=other)}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "SIGNATURE_INVALID"


async def test_tampered_body_401(api: httpx.AsyncClient) -> None:
    hdr = sign_header("POST", "/t/echo", BODY)
    r = await api.post("/t/echo", content=b'{"x":2}', headers={H: hdr})
    assert r.status_code == 401


async def test_tampered_path_or_query_401(api: httpx.AsyncClient) -> None:
    hdr = sign_header("POST", "/t/echo?a=1", BODY)
    r = await api.post("/t/echo?a=2", content=BODY, headers={H: hdr})
    assert r.status_code == 401


async def test_unknown_device_401_same_code(api: httpx.AsyncClient) -> None:
    hdr = sign_header("POST", "/t/echo", BODY, device_id=uuid.UUID(int=99))
    r = await api.post("/t/echo", content=BODY, headers={H: hdr})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "SIGNATURE_INVALID"


@pytest.mark.parametrize(
    "hdr",
    [
        "",
        "garbage",
        "v1;device_id=x;ts=1;sig=abc",
        "v2;device_id=019361a0-0000-7000-8000-000000000001;ts=1;sig=bW9ja21vY2ttb2Nrbw",
    ],
)
async def test_missing_or_malformed_header_401(api: httpx.AsyncClient, hdr: str) -> None:
    headers = {H: hdr} if hdr else {}
    r = await api.post("/t/echo", content=BODY, headers=headers)
    assert r.status_code == 401


async def test_ec_db19_timestamp_window_300s_tolerates_45s_skew(
    api: httpx.AsyncClient,
) -> None:
    """Lệch đồng hồ 45s vẫn qua; lệch > 300s bị từ chối (cùng quy tắc EC-DB19)."""
    ok = sign_header("POST", "/t/echo", BODY, ts=TS - 45)
    assert (await api.post("/t/echo", content=BODY, headers={H: ok})).status_code == 200
    ok_future = sign_header("POST", "/t/echo", BODY, ts=TS + 45)
    assert (await api.post("/t/echo", content=BODY, headers={H: ok_future})).status_code == 200
    old = sign_header("POST", "/t/echo", BODY, ts=TS - 301)
    r = await api.post("/t/echo", content=BODY, headers={H: old})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "TIMESTAMP_OUT_OF_WINDOW"


async def test_replayed_signature_rejected(api: httpx.AsyncClient) -> None:
    hdr = sign_header("POST", "/t/echo", BODY)
    assert (await api.post("/t/echo", content=BODY, headers={H: hdr})).status_code == 200
    again = await api.post("/t/echo", content=BODY, headers={H: hdr})
    assert again.status_code == 401


def test_der_spki_public_key_supported() -> None:
    from app.security.device_auth import load_device_public_key

    der = PRIV.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    assert load_device_public_key(der).public_numbers() == PRIV.public_key().public_numbers()


def test_non_p256_key_rejected() -> None:
    from app.core.errors import AppError
    from app.security.device_auth import load_device_public_key

    k = ec.generate_private_key(ec.SECP384R1()).public_key()
    der = k.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(AppError):
        load_device_public_key(der)
    with pytest.raises(AppError):
        load_device_public_key(b"\x04" + b"\x00" * 64)


def test_parsed_repr_hides_signature() -> None:
    hdr = sign_header("GET", "/x", b"")
    parsed = parse_device_signature_header(hdr)
    assert hdr.split("sig=")[1] not in repr(parsed)
    assert isinstance(VerifiedDevice(DEVICE_ID, TS), VerifiedDevice)


def test_canonical_message_format() -> None:
    msg = canonical_device_message("post", "/api/v1/x?y=1", 1795543200, b"")
    assert msg == (b"POST\n/api/v1/x?y=1\n1795543200\n" + hashlib.sha256(b"").hexdigest().encode())


# ------------------------------------------------------- review T10 fixes ---

P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _malleate(header: str) -> str:
    """(r, s) → (r, n - s): vẫn là chữ ký ECDSA hợp lệ cho cùng thông điệp."""
    prefix, sig_b64 = header.split("sig=")
    der = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    r, s = decode_dss_signature(der)
    return prefix + "sig=" + _b64url(encode_dss_signature(r, P256_N - s))


async def test_replay_with_malleated_s_rejected(api: httpx.AsyncClient) -> None:
    hdr = sign_header("POST", "/t/echo", BODY)
    assert (await api.post("/t/echo", content=BODY, headers={H: hdr})).status_code == 200
    malleated = _malleate(hdr)
    assert malleated != hdr
    # chữ ký biến đổi vẫn hợp lệ về toán học...
    parsed = parse_device_signature_header(malleated)
    PRIV.public_key().verify(
        parsed.signature,
        canonical_device_message("POST", "/t/echo", TS, BODY),
        ec.ECDSA(hashes.SHA256()),
    )
    # ...nhưng khóa chống phát lại theo (device, thông điệp) nên vẫn bị chặn
    r = await api.post("/t/echo", content=BODY, headers={H: malleated})
    assert r.status_code == 401


async def test_replay_as_p1363_encoding_rejected(api: httpx.AsyncClient) -> None:
    """Cùng chữ ký nhưng đổi mã hóa DER → r||s cũng không phát lại được."""
    hdr = sign_header("POST", "/t/echo", BODY)
    assert (await api.post("/t/echo", content=BODY, headers={H: hdr})).status_code == 200
    prefix, sig_b64 = hdr.split("sig=")
    r_, s_ = decode_dss_signature(base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4)))
    p1363 = prefix + "sig=" + _b64url(r_.to_bytes(32, "big") + s_.to_bytes(32, "big"))
    assert (await api.post("/t/echo", content=BODY, headers={H: p1363})).status_code == 401


def test_replay_key_bound_to_device_and_message() -> None:
    m1 = canonical_device_message("POST", "/a", TS, b"x")
    m2 = canonical_device_message("POST", "/a", TS, b"y")
    assert replay_key(DEVICE_ID, m1) != replay_key(DEVICE_ID, m2)
    assert replay_key(DEVICE_ID, m1) != replay_key(uuid.UUID(int=5), m1)
    assert replay_key(DEVICE_ID, m1).startswith(f"vs:sig:dev:{DEVICE_ID}:")


def test_64_byte_der_signature_treated_as_der_first() -> None:
    """DER hợp lệ dài đúng 64 byte không bị hiểu nhầm là P1363 r||s."""
    der = encode_dss_signature(2**227 + 12345, 2**227 + 67890)  # r,s 29 byte → DER 64 byte
    assert len(der) == 64 and der[0] == 0x30
    assert _to_der(der) == der
    raw = (1).to_bytes(32, "big") + (2).to_bytes(32, "big")  # không phải DER
    assert decode_dss_signature(_to_der(raw)) == (1, 2)


async def test_no_redis_outside_local_test_fails_closed_503(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import redis as core_redis
    from app.core.config import AppEnv
    from app.security import device_auth

    from ._helpers import make_settings

    core_redis.set_redis_client(None)
    monkeypatch.setattr(core_redis, "init_redis", lambda settings=None: None)
    app = FastAPI()
    install_error_handlers(app)

    @app.post("/t/echo")
    async def echo(device: VerifiedDeviceCaller) -> dict[str, str]:
        return {"device_id": str(device.device_id)}

    async def resolver(device_id: uuid.UUID) -> bytes | None:
        return PUB_POINT

    app.dependency_overrides[get_clock] = lambda: clock
    app.dependency_overrides[get_device_key_resolver] = lambda: resolver
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        for env, expected in ((AppEnv.STAGING, 503), (AppEnv.TEST, 200)):
            monkeypatch.setattr(device_auth, "get_settings", lambda e=env: make_settings(app_env=e))
            hdr = sign_header("POST", "/t/echo", BODY)
            r = await c.post("/t/echo", content=BODY, headers={H: hdr})
            assert r.status_code == expected, (env, r.text)
