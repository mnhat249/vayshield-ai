"""Khóa/bí mật GIẢ cho test bảo mật (không phải bí mật thật)."""

from __future__ import annotations

import base64

from pydantic import SecretStr

from app.core.config import AppEnv, Settings

KEK_A = bytes(range(32))
KEK_B = bytes(range(100, 132))
ED25519_SEED = bytes([7]) * 32
PEPPER_PHONE_V1 = "phone-pepper-v1-" + "a" * 32
PEPPER_PHONE_V2 = "phone-pepper-v2-" + "b" * 32
PEPPER_MULE_V1 = "mule-pepper-v1-" + "c" * 32
PEPPER_MULE_V2 = "mule-pepper-v2-" + "d" * 32


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": AppEnv.TEST,
        "database_url": None,
        "redis_url": None,
        "log_json": False,
        "kms_master_kek": SecretStr(b64(KEK_A)),
        "kms_master_kek_prev": None,
        "ed25519_policy_private_key": SecretStr(b64(ED25519_SEED)),
        "ed25519_policy_key_id": "policy-test-k1",
        "pepper_b2b_phone": SecretStr(PEPPER_PHONE_V1),
        "pepper_b2b_phone_prev": None,
        "pepper_b2b_phone_version": 1,
        "pepper_mule_account": SecretStr(PEPPER_MULE_V1),
        "pepper_mule_account_prev": None,
        "pepper_mule_account_version": 1,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]
