"""Settings: đủ biến môi trường, parse CSV, chặn cấu hình nguy hiểm ở production."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import AppEnv, Settings

REQUIRED_ENV_FIELDS = {
    "database_url",
    "redis_url",
    "gemini_api_keys",
    "gemini_vip_keys",
    "pepper_b2b_phone",
    "pepper_b2b_phone_prev",
    "pepper_mule_account",
    "pepper_mule_account_prev",
    "kms_master_kek",
    "ed25519_policy_private_key",
    "vapid_public_key",
    "vapid_private_key",
    "vapid_subject",
    "sms_provider",
    "sms_api_key",
    "cors_origins",
    "sandbox_enabled",
    "sandbox_vip_code",
    "sandbox_daily_budget_usd",
}


def test_settings_declares_all_plan_env_vars() -> None:
    assert set(Settings.model_fields) >= REQUIRED_ENV_FIELDS


def test_csv_lists_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEYS", "k1, k2,,k3")
    monkeypatch.setenv("CORS_ORIGINS", "https://vayshield.example")
    cfg = Settings()
    assert [k.get_secret_value() for k in cfg.gemini_api_keys] == ["k1", "k2", "k3"]
    assert cfg.cors_origins == ["https://vayshield.example"]


def test_secrets_not_leaked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEPPER_B2B_PHONE", "super-secret-pepper")
    cfg = Settings()
    assert "super-secret-pepper" not in repr(cfg)
    assert cfg.pepper_b2b_phone is not None
    assert cfg.pepper_b2b_phone.get_secret_value() == "super-secret-pepper"


def test_gemini_keys_not_leaked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEYS", "AIzaSyPUBLICKEY111,AIzaSyPUBLICKEY222")
    monkeypatch.setenv("GEMINI_VIP_KEYS", "AIzaSyVIPKEY333")
    cfg = Settings()
    text = repr(cfg) + str(cfg) + cfg.model_dump_json()
    for key in ("AIzaSyPUBLICKEY111", "AIzaSyPUBLICKEY222", "AIzaSyVIPKEY333"):
        assert key not in text
    assert cfg.gemini_vip_keys[0].get_secret_value() == "AIzaSyVIPKEY333"


def test_production_requires_secrets() -> None:
    with pytest.raises(ValidationError, match="PEPPER_B2B_PHONE"):
        Settings(app_env=AppEnv.PRODUCTION)


def _prod_kwargs() -> dict[str, object]:
    return {
        "app_env": AppEnv.PRODUCTION,
        "database_url": "postgresql+asyncpg://u:p@db/x",
        "redis_url": "redis://r:6379/0",
        "pepper_b2b_phone": "p1",
        "pepper_mule_account": "p2",
        "kms_master_kek": "k",
        "ed25519_policy_private_key": "e",
        "jwt_ops_secret": "j",
        "cors_origins": ["https://vayshield.example"],
    }


def test_ec_db24_production_forbids_wildcard_cors() -> None:
    kwargs = {**_prod_kwargs(), "cors_origins": ["*"]}
    with pytest.raises(ValidationError, match="CORS"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_ec_ops23_production_forbids_error_details() -> None:
    kwargs = {**_prod_kwargs(), "expose_error_details": True}
    with pytest.raises(ValidationError, match="EXPOSE_ERROR_DETAILS"):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_production_ok_with_all_secrets() -> None:
    cfg = Settings(**_prod_kwargs())  # type: ignore[arg-type]
    assert cfg.is_production
