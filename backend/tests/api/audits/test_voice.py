"""POST /audits/voice-terms (File 3 §1.3): Voice-to-Cashflow, không đoán số mơ hồ."""

from __future__ import annotations

import io
import json
from typing import Any

import httpx

from app.core.clock import FakeClock
from app.core.schemas.audits import VoiceTermsResponse

from ._helpers import VOICE, signed_multipart
from .conftest import FakeStt

FULL = "vay mười củ rưỡi, thực nhận tám triệu tư, góp hai xị rưỡi một ngày, góp ba tuần"
WEEKLY = "mượn tám củ rưỡi, mỗi tuần đóng chín xị rưỡi, trong ba tháng"


def _meta(**over: Any) -> str:
    return json.dumps({"locale": "vi-VN", **over})


async def _post(
    client: httpx.AsyncClient,
    clock: FakeClock,
    *,
    transcript: str | None = None,
    audio: bytes | None = None,
    mime: str = "audio/webm",
) -> httpx.Response:
    files = [("audio", ("a.webm", io.BytesIO(audio), mime))] if audio is not None else None
    data = {"metadata_json": _meta()}
    if transcript is not None:
        data["transcript_text"] = transcript
    return await signed_multipart(client, clock, VOICE, files=files, data=data)


async def test_transcript_gives_normalized_terms_and_provisional_verdict(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await _post(client, fake_clock, transcript=WEEKLY)
    assert r.status_code == 200, r.text
    body = VoiceTermsResponse.model_validate(r.json())
    assert body.normalized_terms.nominal_principal_vnd == 8_500_000
    assert body.normalized_terms.tenure_days == 90
    assert [t.value_vnd for t in body.slang_tokens] == [8_500_000, 950_000]
    assert body.needs_confirmation is True  # lời nói luôn phải xác nhận tay
    assert body.verdict.apr_vn_365 is not None
    assert body.verdict.verdict_code.value == "V_RED_CRIMINAL_USURY"
    assert body.audit_id in env.audits.audits
    assert len(env.gemini.calls) == 0  # tất định, không gọi LLM


async def test_inconsistent_spoken_numbers_do_not_crash_or_guess(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """Thực nhận 8,4tr nhưng chỉ trả 5,25tr (lãi âm) → APR không xác định, AMBER, không 500."""
    r = await _post(client, fake_clock, transcript=FULL)
    assert r.status_code == 200
    assert r.json()["verdict"]["apr_vn_365"] is None
    assert r.json()["verdict"]["verdict_code"] == "V_AMBER_INCOMPLETE_DOC"


async def test_ambiguous_amount_not_guessed_and_asks_again(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    """EC-AI11 ở tầng API: cụm tiền mơ hồ bị loại khỏi ``slang_tokens`` (contract cần số), APR
    "không xác định" (AMBER) và ``needs_confirmation``."""
    r = await _post(client, fake_clock, transcript="vay mấy củ, phải trả 3 triệu 30, trong 30 ngày")
    body = r.json()
    assert body["needs_confirmation"] is True
    assert body["normalized_terms"]["nominal_principal_vnd"] is None
    assert body["verdict"]["apr_vn_365"] is None
    assert body["verdict"]["verdict_code"] == "V_AMBER_INCOMPLETE_DOC"
    assert all(isinstance(t["value_vnd"], int) for t in body["slang_tokens"])


async def test_transcript_pii_is_masked_but_amounts_survive(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    text = f"{WEEKLY}, gọi số 0909123456 hoặc mã OTP 482913, chuyển vào tài khoản 1903123498899"
    r = await _post(client, fake_clock, transcript=text)
    masked = r.json()["transcript_masked"]
    for secret in ("0909123456", "482913", "1903123498899"):
        assert secret not in masked
    assert "[SĐT]" in masked and "[OTP]" in masked
    stored = json.dumps(next(iter(env.audits.audits.values())).context, ensure_ascii=False)
    assert "0909123456" not in stored


async def test_upfront_fee_by_voice_is_red(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await _post(
        client,
        fake_clock,
        transcript="bắt nộp trước phí bảo hiểm hai củ trước khi giải ngân, vay mười củ",
    )
    verdict = r.json()["verdict"]
    assert verdict["verdict_code"] == "V_RED_ADVANCE_FEE"
    assert verdict["kernel_signal"] == "INFINITE_APR_ADVANCE_FEE_SCAM"
    assert r.json()["normalized_terms"]["upfront_fee_amount_vnd"] == 2_000_000


async def test_audio_without_transcript_uses_stt_port(
    build_env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    stt = FakeStt(WEEKLY)
    build_env(stt=stt)
    r = await _post(client, fake_clock, audio=b"\x1aE\xdf\xa3fake-webm")
    assert r.status_code == 200, r.text
    assert stt.calls == 1
    assert r.json()["normalized_terms"]["nominal_principal_vnd"] == 8_500_000


async def test_audio_without_stt_is_503_manual_fallback(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    r = await _post(client, fake_clock, audio=b"\x1aE\xdf\xa3fake-webm")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "SERVICE_DEGRADED"


async def test_voice_validation_and_limits(
    env: Any, client: httpx.AsyncClient, fake_clock: FakeClock
) -> None:
    neither = await signed_multipart(client, fake_clock, VOICE, data={"metadata_json": _meta()})
    assert neither.status_code == 422
    bad_type = await _post(client, fake_clock, audio=b"x", mime="video/mp4")
    assert bad_type.status_code == 415
    fake_clock.advance(1)
    too_big = await _post(
        client, fake_clock, audio=b"\0" * (env.services.settings.upload_max_audio_bytes + 1)
    )
    assert too_big.status_code in (413,)
    unsigned = await client.post(VOICE, data={"metadata_json": _meta(), "transcript_text": WEEKLY})
    assert unsigned.status_code == 401
