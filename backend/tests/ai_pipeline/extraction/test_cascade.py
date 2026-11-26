"""``ModelCascade`` (File 2 §3.1 tầng 1–2): S2-EC46, xoay vòng key, EC-AI22."""

from __future__ import annotations

import time

import pytest
from pydantic import SecretStr

from ai_pipeline.extraction.cascade import (
    CascadeConfig,
    ExtractionUnavailableError,
    downscale_image_part,
)
from ai_pipeline.extraction.gemini_client import (
    ApiKeyPool,
    FakeReply,
    GeminiAuthError,
    GeminiBadRequestError,
    GeminiQuotaError,
    GeminiTransientError,
    ImagePart,
    NoApiKeyAvailableError,
    reply_json,
)
from ai_pipeline.extraction.prompts import SYSTEM_PROMPT_V2, build_user_parts
from app.core.clock import FakeClock
from app.core.config import Settings

from ._factory import IMG, PRIMARY, PRO, SECONDARY, loan, make_cascade

PARTS = build_user_parts([IMG])


def test_s2_ec46_default_timeouts() -> None:
    """S2-EC46: soft-timeout 4.5s; File 2 §3.1: failover 6s."""
    cfg = CascadeConfig()
    assert cfg.soft_timeout_s == 4.5
    assert cfg.failover_timeout_s == 6.0
    from_settings = CascadeConfig.from_settings(Settings())
    assert from_settings.soft_timeout_s == 4.5
    assert from_settings.failover_timeout_s == 6.0
    assert from_settings.primary_model == Settings().gemini_model_primary


async def test_primary_ok() -> None:
    cascade, client, _ = make_cascade({PRIMARY: [reply_json(loan())]})
    result = await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    assert result.model_used == PRIMARY
    assert result.extraction.nominal_principal_vnd == 10_000_000
    assert not result.soft_timeout_fired
    assert client.models_called() == [PRIMARY]


async def test_s2_ec46_soft_timeout_fires_but_primary_still_wins() -> None:
    """S2-EC46: quá 4.5s (thu nhỏ: 0.05s) → báo OCR dự phòng chạy song song, vẫn chờ Pha 1."""
    fired: list[bool] = []
    cascade, client, _ = make_cascade({PRIMARY: [reply_json(loan(), delay_s=0.1)]})
    result = await cascade.extract(
        system_instruction=SYSTEM_PROMPT_V2, parts=PARTS, on_soft_timeout=lambda: fired.append(True)
    )
    assert fired == [True]
    assert result.soft_timeout_fired
    assert result.model_used == PRIMARY
    assert client.models_called() == [PRIMARY]


async def test_s2_ec46_failover_to_secondary_after_hard_timeout() -> None:
    """S2-EC46 / File 2 §3.1: quá 6s (thu nhỏ: 0.2s) → hủy, chuyển Flash-Lite."""
    fired: list[bool] = []
    cascade, client, _ = make_cascade(
        {
            PRIMARY: [reply_json(loan(), delay_s=5.0)],
            SECONDARY: [reply_json(loan(net_disbursed_vnd=8_400_000))],
        }
    )
    started = time.monotonic()
    result = await cascade.extract(
        system_instruction=SYSTEM_PROMPT_V2, parts=PARTS, on_soft_timeout=lambda: fired.append(True)
    )
    assert time.monotonic() - started < 1.0
    assert result.model_used == SECONDARY
    assert result.extraction.net_disbursed_vnd == 8_400_000
    assert fired == [True]
    assert [(a.model, a.outcome) for a in result.attempts] == [
        (PRIMARY, "timeout"),
        (SECONDARY, "ok"),
    ]
    assert client.models_called() == [PRIMARY, SECONDARY]


async def test_secondary_gets_downscaled_images() -> None:
    seen: list[ImagePart] = []

    def shrink(p: ImagePart) -> ImagePart:
        small = ImagePart(b"small", p.mime_type)
        seen.append(small)
        return small

    cascade, client, _ = make_cascade(
        {PRIMARY: [FakeReply(error=GeminiBadRequestError("400"))], SECONDARY: [reply_json(loan())]}
    )
    cascade._downscale = shrink
    await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    secondary_req = client.calls[-1].request
    assert any(p is seen[0] for p in secondary_req.parts)


def test_downscale_real_image_to_960() -> None:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2000, 1000), "white").save(buf, format="PNG")
    out = downscale_image_part(ImagePart(buf.getvalue(), "image/png"))
    with Image.open(io.BytesIO(out.data)) as img:
        assert max(img.size) == 960
    broken = ImagePart(b"broken")
    assert downscale_image_part(broken) is broken


async def test_key_rotation_on_quota() -> None:
    """429 trên key 0 → thử key 1 cùng model; key 0 nghỉ cooldown."""
    cascade, client, pool = make_cascade(
        {PRIMARY: [FakeReply(error=GeminiQuotaError("429")), reply_json(loan())]}, keys=2
    )
    result = await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    assert result.model_used == PRIMARY
    assert [c.key_index_hint for c in client.calls] == ["0000", "0001"]
    assert [a.outcome for a in result.attempts] == ["quota", "ok"]
    # key 0 đang nghỉ → lần kế dùng key 1
    assert pool.acquire().index == 1


async def test_auth_error_disables_key_and_transient_rotates() -> None:
    cascade, client, pool = make_cascade(
        {
            PRIMARY: [
                FakeReply(error=GeminiAuthError("403")),
                FakeReply(error=GeminiTransientError("503")),
                reply_json(loan()),
            ]
        },
        keys=3,
    )
    result = await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    assert [a.outcome for a in result.attempts] == ["auth", "transient", "ok"]
    assert [c.key_index_hint for c in client.calls] == ["0000", "0001", "0002"]
    assert {pool.acquire().index for _ in range(4)} == {1, 2}


async def test_all_keys_exhausted_falls_to_secondary_then_unavailable() -> None:
    cascade, _, _ = make_cascade(
        {PRIMARY: [FakeReply(error=GeminiQuotaError("429"))] * 2},
        keys=2,
    )
    with pytest.raises(ExtractionUnavailableError) as exc:
        await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    outcomes = [(a.model, a.outcome) for a in exc.value.attempts]
    assert outcomes == [(PRIMARY, "quota"), (PRIMARY, "quota"), (SECONDARY, "no_key")]


def test_api_key_pool_round_robin_and_cooldown() -> None:
    clock = FakeClock()
    pool = ApiKeyPool([SecretStr("k-aaaa"), SecretStr("k-bbbb")], clock, quota_cooldown_s=60)
    assert [pool.acquire().index for _ in range(4)] == [0, 1, 0, 1]
    lease = pool.acquire()
    pool.report_quota_exhausted(lease)
    assert [pool.acquire().index for _ in range(2)] == [1, 1]
    clock.advance(61)
    assert {pool.acquire().index for _ in range(2)} == {0, 1}
    pool.report_invalid(pool.acquire())
    pool.report_invalid(pool.acquire())
    with pytest.raises(NoApiKeyAvailableError):
        pool.acquire()
    assert "k-aaaa" not in repr(pool)
    assert "k-aaaa" not in repr(lease)


async def test_ec_ai22_truncated_output_retries_secondary_immediately() -> None:
    """EC-AI22: MAX_TOKENS → chuyển model dự phòng ngay (< 1.5s), không backoff."""
    full = loan().model_dump_json()
    cascade, client, _ = make_cascade(
        {
            PRIMARY: [FakeReply(text=full[: len(full) // 2], finish_reason="MAX_TOKENS")],
            SECONDARY: [reply_json(loan())],
        }
    )
    started = time.monotonic()
    result = await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    assert time.monotonic() - started < 1.5
    assert result.model_used == SECONDARY
    assert not result.truncated_repaired
    assert [a.outcome for a in result.attempts] == ["truncated", "ok"]
    assert client.calls[0].request.max_output_tokens >= 8192


async def test_ec_ai22_all_truncated_returns_repaired_flagged() -> None:
    """EC-AI22: mọi model đều cắt cụt nhưng bản sửa hợp lệ → trả kèm cờ để ép xác nhận."""
    full = loan(extraction_notes_vi=["một", "hai"]).model_dump_json()
    cut = full[: full.rfind('"hai"') + 2]
    cascade, _, _ = make_cascade(
        {
            PRIMARY: [FakeReply(text=cut, finish_reason="MAX_TOKENS")],
            SECONDARY: [FakeReply(text=cut, finish_reason="MAX_TOKENS")],
        }
    )
    result = await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    assert result.truncated_repaired
    assert result.extraction.nominal_principal_vnd == 10_000_000
    assert result.extraction.extraction_notes_vi == ["một"]


async def test_ec_ai22_unrepairable_raises() -> None:
    cascade, _, _ = make_cascade(
        {
            PRIMARY: [FakeReply(text='{"screen_categories": ["CONTR', finish_reason="MAX_TOKENS")],
            SECONDARY: [FakeReply(text="not json")],
        }
    )
    with pytest.raises(ExtractionUnavailableError) as exc:
        await cascade.extract(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    assert [a.outcome for a in exc.value.attempts] == ["truncated", "invalid"]


async def test_escalate_uses_pro_model() -> None:
    cascade, client, _ = make_cascade({PRO: [reply_json(loan())]})
    result = await cascade.escalate(system_instruction=SYSTEM_PROMPT_V2, parts=PARTS)
    assert result.model_used == PRO
    assert client.models_called() == [PRO]
