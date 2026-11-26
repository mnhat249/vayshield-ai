"""Log/thông điệp lỗi không chứa PII hay khóa (File 4 §5.2 Luật 91/2025, DoD T10)."""

from __future__ import annotations

import logging
import uuid

import fakeredis
import pytest
from pydantic import SecretStr

from app.core.errors import AppError
from app.security.blind_index import BlindIndexKind, blind_index
from app.security.device_auth import mark_signature_used, parse_device_signature_header
from app.security.envelope import EnvelopeCipher, record_aad
from app.security.erasure import erase_device
from app.security.phone import PhoneNormalizationError, normalize_phone_e164
from app.security.signing import Ed25519Signer

from ._helpers import ED25519_SEED, KEK_A, PEPPER_PHONE_V1, b64, make_settings
from .test_erasure import FakeConn

PHONE = "0909123456"


async def test_security_logs_and_errors_contain_no_pii_or_keys(
    caplog: pytest.LogCaptureFixture, redis: fakeredis.FakeAsyncRedis
) -> None:
    caplog.set_level(logging.DEBUG)
    dev = uuid.UUID("019361a0-0000-7000-8000-0000000000bb")
    messages: list[str] = []

    blind_index(BlindIndexKind.PHONE, PHONE, settings=make_settings())
    for bad in ("0909-12345", "+84 1999 999 999 999"):
        try:
            normalize_phone_e164(bad)
        except PhoneNormalizationError as exc:
            messages.append(str(exc))
    try:
        parse_device_signature_header(f"v1;device_id={dev};ts=1;sig=%%%")
    except AppError as exc:
        messages.append(str(exc))
    await erase_device(FakeConn(exists=True, edges=1, partners=[]), redis, dev)  # type: ignore[arg-type]

    class BrokenRedis:
        async def set(self, *a: object, **k: object) -> None:
            raise ConnectionError(f"redis down for {PHONE}")

    with pytest.raises(AppError):
        await mark_signature_used(BrokenRedis(), b"sig-bytes", 10)  # type: ignore[arg-type]

    cipher = EnvelopeCipher(KEK_A)
    blob = cipher.encrypt(PHONE.encode(), aad=record_aad("t", "c", dev))
    messages += [repr(cipher), repr(Ed25519Signer(ED25519_SEED, "k1")), repr(make_settings())]

    corpus = "\n".join(messages + [r.getMessage() for r in caplog.records] + [caplog.text])
    forbidden = [
        PHONE,
        "+84909123456",
        str(dev),
        PEPPER_PHONE_V1,
        KEK_A.hex(),
        b64(KEK_A),
        ED25519_SEED.hex(),
        b64(ED25519_SEED),
        blob.hex(),
    ]
    for value in forbidden:
        assert value not in corpus, value
    assert any("erasure:" in r.getMessage() for r in caplog.records)


def test_secretstr_settings_do_not_leak() -> None:
    cfg = make_settings(pepper_b2b_phone=SecretStr(PEPPER_PHONE_V1))
    assert PEPPER_PHONE_V1 not in repr(cfg) + str(cfg.model_dump())
