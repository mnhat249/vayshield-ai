"""Dữ liệu dựng sẵn cho test trích xuất (không gọi mạng)."""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from ai_pipeline.extraction.cascade import CascadeConfig, ModelCascade
from ai_pipeline.extraction.gemini_client import (
    ApiKeyPool,
    FakeGeminiClient,
    FakeReply,
    ImagePart,
)
from ai_pipeline.extraction.schemas import MultiImageLoanExtraction
from app.core.clock import FakeClock

PRIMARY = "flash"
SECONDARY = "flash-lite"
PRO = "pro"
IMG = ImagePart(b"not-a-real-image", "image/jpeg")


def loan(**overrides: Any) -> MultiImageLoanExtraction:
    base: dict[str, Any] = {
        "screen_categories": ["CONTRACT_TERMS"],
        "claimed_lender_name": "App Vay Nhanh A",
        "nominal_principal_vnd": 10_000_000,
        "net_disbursed_vnd": 8_500_000,
        "total_repayment_stated_vnd": 12_000_000,
        "tenure_days_total": 90,
        "repayment_regimes": [
            {"period_count": 3, "period_interval_days": 30, "amount_per_period_vnd": 4_000_000}
        ],
        "deducted_fees_at_source": [{"fee_label": "Phí hồ sơ", "amount_vnd": 1_500_000}],
        "upfront_fee_demand": {"demanded": False},
        "critical_fields_min_confidence": 0.95,
    }
    base.update(overrides)
    return MultiImageLoanExtraction.model_validate(base)


def make_cascade(
    script: dict[str, list[FakeReply]],
    *,
    keys: int = 2,
    soft: float = 0.05,
    failover: float = 0.2,
) -> tuple[ModelCascade, FakeGeminiClient, ApiKeyPool]:
    client = FakeGeminiClient(script=script)
    pool = ApiKeyPool([SecretStr(f"test-key-{i:04d}") for i in range(keys)], FakeClock())
    config = CascadeConfig(
        primary_model=PRIMARY,
        secondary_model=SECONDARY,
        escalation_model=PRO,
        soft_timeout_s=soft,
        failover_timeout_s=failover,
        secondary_timeout_s=failover,
        escalation_timeout_s=failover,
    )
    return ModelCascade(client, pool, config, downscaler=lambda p: p), client, pool
