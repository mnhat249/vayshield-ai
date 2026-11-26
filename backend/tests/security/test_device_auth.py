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
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import FastAPI

from app.core.clock import FakeClock, get_clock
from app.core.errors import install_error_handlers
from app.security.device_auth import (
    VerifiedDevice,
    VerifiedDeviceCaller,
    canonical_device_message,
    get_device_key_resolver,
    parse_device_signature_header,
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
