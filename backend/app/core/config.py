"""Cấu hình toàn backend qua biến môi trường (pydantic-settings).

D5: file này khai báo ĐỦ mọi biến môi trường cho toàn plan. Task sau không thêm biến;
cần biến mới → ghi "Cần quyết định". Bí mật dùng ``SecretStr`` để không lộ khi log/repr.
Giá trị mặc định chỉ dành cho môi trường local/test; ``APP_ENV=production`` bắt buộc khai đủ.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]
CsvSecretList = Annotated[list[SecretStr], NoDecode]  # repr không lộ key


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


def _split_csv(value: Any) -> Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    # --- Ứng dụng ---------------------------------------------------------
    app_env: AppEnv = AppEnv.LOCAL
    app_version: str = "0.1.0"
    app_public_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    log_json: bool = True
    expose_error_details: bool = False  # EC-OPS23: luôn False ở production

    # --- PostgreSQL 16 ----------------------------------------------------
    database_url: SecretStr | None = None  # postgresql+asyncpg://user:pass@host:5432/db
    db_pool_size: int = Field(default=10, ge=1, le=200)
    db_max_overflow: int = Field(default=10, ge=0, le=200)
    db_pool_timeout_s: float = Field(default=5.0, gt=0)
    db_statement_timeout_ms: int = Field(default=3000, ge=100)
    db_healthcheck_timeout_s: float = Field(default=0.5, gt=0)

    # --- Redis 7 ----------------------------------------------------------
    redis_url: SecretStr | None = None  # redis://host:6379/0
    redis_socket_timeout_ms: int = Field(default=80, ge=10)  # EC-DB11: > 80ms → fallback PG

    # --- Gemini (File 1 ADR-04, File 6 §1.2 mục 3) ------------------------
    gemini_api_keys: CsvSecretList = Field(default_factory=list)  # pool công cộng
    gemini_vip_keys: CsvSecretList = Field(default_factory=list)  # pool riêng giám khảo
    gemini_model_primary: str = "gemini-2.5-flash"
    gemini_model_escalation: str = "gemini-2.5-pro"
    gemini_model_secondary: str = "gemini-2.5-flash-lite"
    gemini_soft_timeout_s: float = Field(default=4.5, gt=0)  # S2-EC46
    gemini_failover_timeout_s: float = Field(default=6.0, gt=0)  # File 2 §3.1
    gemini_daily_budget_usd: float = Field(default=5.0, ge=0)
    gemini_vip_daily_budget_usd: float = Field(default=20.0, ge=0)

    # --- Blind index / mật mã (File 4 §5.1, EC-DB08) ----------------------
    pepper_b2b_phone: SecretStr | None = None
    pepper_b2b_phone_prev: SecretStr | None = None
    pepper_b2b_phone_version: int = Field(default=1, ge=1)
    pepper_mule_account: SecretStr | None = None
    pepper_mule_account_prev: SecretStr | None = None
    pepper_mule_account_version: int = Field(default=1, ge=1)
    kms_master_kek: SecretStr | None = None  # base64 32 byte, bọc DEK AES-256-GCM
    kms_master_kek_prev: SecretStr | None = None
    ed25519_policy_private_key: SecretStr | None = None  # base64 seed 32 byte
    ed25519_policy_key_id: str = "policy-k1"

    # --- Web Push VAPID ---------------------------------------------------
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "mailto:security@example.invalid"

    # --- SMS / Email fallback (File 4 §4.2(C)) ----------------------------
    sms_provider: str = "fake"  # fake | <tên nhà cung cấp>
    sms_api_base_url: str | None = None
    sms_api_key: SecretStr | None = None
    sms_api_secret: SecretStr | None = None
    sms_sender_id: str = "VAYSHIELD"
    email_provider: str = "fake"
    email_api_key: SecretStr | None = None
    email_from: str = "no-reply@example.invalid"

    # --- CORS (EC-DB24, EC-OPS18) -----------------------------------------
    cors_origins: CsvList = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # --- Sandbox Mode (File 4 EC-DB16, File 6 §1.2 mục 3, EC-OPS10) -------
    sandbox_enabled: bool = True
    sandbox_vip_code: SecretStr | None = None  # mã trên tờ A4 (vd ?vip=...)
    sandbox_session_ttl_s: int = Field(default=7200, ge=60)
    sandbox_namespace_prefix: str = "sbx_"
    sandbox_daily_budget_usd: float = Field(default=2.0, ge=0)
    sandbox_max_sessions_per_ip_per_hour: int = Field(default=30, ge=1)

    # --- B2B webhook (File 4 §4.2(B), EC-DB19) ----------------------------
    b2b_signature_window_s: int = Field(default=300, ge=1)
    b2b_idempotency_ttl_s: int = Field(default=48 * 3600, ge=60)

    # --- Ops RBAC JWT (File 4 §4.1, EC-OPS15) -----------------------------
    jwt_ops_secret: SecretStr | None = None
    jwt_ops_issuer: str = "vayshield-ops"
    jwt_ops_access_ttl_s: int = Field(default=900, ge=60)
    jwt_ops_refresh_ttl_s: int = Field(default=30 * 24 * 3600, ge=3600)
    judge_ops_quick_login_code: SecretStr | None = None

    # --- Realtime (File 4 §3.1, §4.1, §4.2(C)) ----------------------------
    ws_ticket_ttl_s: int = Field(default=45, ge=5)
    ws_ping_interval_s: int = Field(default=20, ge=1)
    escalation_ack_timeout_s: int = Field(default=10, ge=1)
    guardian_session_ttl_s: int = Field(default=180 * 24 * 3600, ge=3600)
    pairing_code_ttl_s: int = Field(default=600, ge=60)
    pairing_max_fail_attempts: int = Field(default=3, ge=1)

    # --- SSE (EC-DB22) ----------------------------------------------------
    sse_keepalive_interval_s: float = Field(default=2.5, gt=0)

    # --- Rate limit (EC-DB25) ---------------------------------------------
    rate_limit_device_rpm: int = Field(default=60, ge=1)
    rate_limit_ip_rpm: int = Field(default=3000, ge=1)  # ~50× device cho CGNAT
    pow_difficulty_bits: int = Field(default=18, ge=0, le=32)

    # --- Upload (File 4 §4.2(A), EC-DB05) ---------------------------------
    upload_max_images: int = Field(default=4, ge=1)
    upload_max_image_bytes: int = Field(default=1_500_000, ge=1)
    upload_max_audio_bytes: int = Field(default=5_000_000, ge=1)
    # Giới hạn body tầng ASGI (EC-DB05): chặn trước khi parser multipart đọc.
    body_limit_default_bytes: int = Field(default=1_048_576, ge=1024)  # 1 MiB
    body_limit_contract_stream_bytes: int = Field(
        default=4 * 1_500_000 + 262_144, ge=1024
    )  # 4 ảnh × 1.5MB + metadata/overhead multipart
    body_limit_voice_terms_bytes: int = Field(default=5_000_000 + 262_144, ge=1024)
    image_max_pixels: int = Field(default=25_000_000, ge=1)

    @field_validator("gemini_api_keys", "gemini_vip_keys", "cors_origins", mode="before")
    @classmethod
    def _csv(cls, value: Any) -> Any:
        return _split_csv(value)

    @model_validator(mode="after")
    def _production_guard(self) -> Settings:
        """Ở production: bắt buộc đủ bí mật, cấm CORS ``*``, cấm lộ chi tiết lỗi."""
        if self.app_env is not AppEnv.PRODUCTION:
            return self
        required = {
            "DATABASE_URL": self.database_url,
            "REDIS_URL": self.redis_url,
            "PEPPER_B2B_PHONE": self.pepper_b2b_phone,
            "PEPPER_MULE_ACCOUNT": self.pepper_mule_account,
            "KMS_MASTER_KEK": self.kms_master_kek,
            "ED25519_POLICY_PRIVATE_KEY": self.ed25519_policy_private_key,
            "JWT_OPS_SECRET": self.jwt_ops_secret,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError("Thiếu biến môi trường bắt buộc ở production: " + ", ".join(missing))
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS không được chứa '*' ở production (EC-DB24)")
        if self.expose_error_details:
            raise ValueError("EXPOSE_ERROR_DETAILS phải là false ở production (EC-OPS23)")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings singleton (đọc env một lần). Test có thể gọi ``get_settings.cache_clear()``."""
    return Settings()
