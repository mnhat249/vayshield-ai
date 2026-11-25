"""Gói quy tắc ký số Ed25519 đồng bộ xuống thiết bị (File 4 §4.1, §5.1)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.schemas.common import LegalCategory


class AndroidPackageSig(BaseModel):
    pkg: str
    sha256: str | None = Field(default=None, description="SHA-256 chứng chỉ ký APK (hex)")


class WhitelistedLender(BaseModel):
    entity_code: str
    official_short_name: str
    legal_category: LegalCategory
    official_hotlines: list[str] = Field(default_factory=list)
    official_domains: list[str] = Field(default_factory=list)
    android_packages: list[AndroidPackageSig] = Field(default_factory=list)


class ChallengeChoiceTemplate(BaseModel):
    choice_id: str
    label_vi: str


class ChallengeTemplate(BaseModel):
    """Câu hỏi bất đối xứng Trạm 3 (File 3 §3.2). Client trộn thứ tự lựa chọn theo phiên."""

    challenge_id: str
    prompt_vi: str
    choices: list[ChallengeChoiceTemplate]


class PolicyThresholds(BaseModel):
    p_scam_watch: float = Field(ge=0, le=1)
    p_scam_cooling_off: float = Field(ge=0, le=1)
    cooling_off_seconds: int = Field(ge=1)
    shield_duration_hours: int = Field(ge=1, le=72)
    smurf_window_minutes: int = Field(ge=1)


class PolicyPayload(BaseModel):
    lender_whitelist: list[WhitelistedLender]
    banking_app_packages: list[str]
    dangerous_permission_combos: list[list[str]]
    blacklist_bloom_b64: str | None = None
    thresholds: PolicyThresholds
    challenge_templates: list[ChallengeTemplate]


class PolicyBundle(BaseModel):
    policy_version: int = Field(ge=1)
    issued_at: datetime
    expires_at: datetime
    algorithm: Literal["Ed25519"]
    key_id: str
    payload: PolicyPayload
    signature_b64: str = Field(
        description="Ed25519 trên JSON chuẩn hóa (sort_keys, separators=(',',':')) của payload"
    )
