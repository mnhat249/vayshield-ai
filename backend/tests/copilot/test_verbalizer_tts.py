"""EC-AI26: Vietnamese TTS Verbalizer + endpoint /tts/synthesize-vi."""

from __future__ import annotations

import base64

import httpx
import pytest
from fastapi import FastAPI

from app.core.schemas.triage import TtsRequest
from app.triage.tts import MAX_AUDIO_BYTES, TtsAudio, get_tts_synthesizer, synthesize_vi
from app.triage.verbalizer import normalize_vi, number_to_vi

from .conftest import SANDBOX_HEADERS


@pytest.mark.parametrize(
    ("raw", "spoken"),
    [
        ("12.600.000 VND", "mười hai triệu sáu trăm nghìn đồng"),
        ("12,600,000 VND", "mười hai triệu sáu trăm nghìn đồng"),
        ("2.000.000đ", "hai triệu đồng"),
        ("1.000.001 đồng", "một triệu không trăm linh một đồng"),
        ("5tr", "năm triệu"),
        ("500k", "năm trăm nghìn"),
        ("2,5tr", "hai phẩy năm triệu"),
        ("Theo NHNN", "Theo Ngân hàng Nhà nước"),
        ("lãi 36%/năm", "lãi ba mươi sáu phần trăm mỗi năm"),
        ("ngày 01/12/2025", "ngày một tháng mười hai năm hai nghìn không trăm hai mươi lăm"),
        ("0909123456", "không chín không chín một hai ba bốn năm sáu"),
        ("hotline 1800 588 822", "hotline một tám không không năm tám tám tám hai hai"),
        ("BLHS và BLDS", "Bộ luật Hình sự và Bộ luật Dân sự"),
        (
            "TCTD, CIC, CCCD",
            "tổ chức tín dụng, Trung tâm Thông tin Tín dụng Quốc gia, căn cước công dân",
        ),
    ],
)
def test_ec_ai26_verbalizer_table(raw: str, spoken: str) -> None:
    """EC-AI26: số tiền/ký hiệu đọc thành chữ tự nhiên, không đọc 'chấm không không không'."""
    assert normalize_vi(raw) == spoken


@pytest.mark.parametrize(
    ("n", "spoken"),
    [
        (0, "không"),
        (5, "năm"),
        (10, "mười"),
        (15, "mười lăm"),
        (21, "hai mươi mốt"),
        (25, "hai mươi lăm"),
        (101, "một trăm linh một"),
        (110, "một trăm mười"),
        (1000, "một nghìn"),
        (1_005, "một nghìn không trăm linh năm"),
        (2_000_000_000, "hai tỷ"),
        (1_000_000_000_000, "một nghìn tỷ"),
    ],
)
def test_ec_ai26_number_to_vi(n: int, spoken: str) -> None:
    assert number_to_vi(n) == spoken


def test_ec_ai26_verbalizer_output_has_no_digits_and_is_idempotent() -> None:
    text = "Chuyển 12.600.000 VND, lãi 120%/năm, gọi 0909123456 trước 01/12/2025 theo NHNN"
    once = normalize_vi(text)
    assert not any(c.isdigit() for c in once)
    assert normalize_vi(once) == once


def test_ec_ai26_control_chars_removed() -> None:
    assert normalize_vi("a\x00b\x07c") == "a b c"


async def test_ec_ai26_tts_endpoint_normalizes_and_falls_back_without_provider(
    sandbox_app: FastAPI, client: httpx.AsyncClient
) -> None:
    r = await client.post(
        "/api/v1/tts/synthesize-vi",
        json={"text_vi": "Chuyển 12.600.000 VND ngay"},
        headers=SANDBOX_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["normalized_text_vi"] == "Chuyển mười hai triệu sáu trăm nghìn đồng ngay"
    assert body["engine"] == "CLIENT_FALLBACK"
    assert body.get("audio_base64") is None


async def test_tts_endpoint_requires_auth(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/tts/synthesize-vi", json={"text_vi": "xin chào"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


class _FakeSynth:
    def __init__(self, data: bytes = b"RIFFfakeaudio") -> None:
        self.data = data
        self.calls: list[str] = []

    async def synthesize(self, text_vi: str, voice: str, speaking_rate: float) -> TtsAudio:
        self.calls.append(text_vi)
        return TtsAudio(mime="audio/wav", data=self.data)


class _BoomSynth:
    async def synthesize(self, text_vi: str, voice: str, speaking_rate: float) -> TtsAudio:
        raise RuntimeError("provider down")


async def test_ec_ai26_server_tts_receives_normalized_text_not_raw() -> None:
    synth = _FakeSynth()
    out = await synthesize_vi(TtsRequest(text_vi="Phí 500k"), synth)
    assert out.engine == "SERVER_TTS"
    assert synth.calls == ["Phí năm trăm nghìn"]
    assert base64.b64decode(out.audio_base64 or "") == synth.data
    assert out.audio_mime == "audio/wav"


async def test_tts_provider_failure_or_oversize_degrades_to_client_fallback() -> None:
    boom = await synthesize_vi(TtsRequest(text_vi="xin chào"), _BoomSynth())
    assert boom.engine == "CLIENT_FALLBACK" and boom.audio_base64 is None
    big = await synthesize_vi(
        TtsRequest(text_vi="xin chào"), _FakeSynth(b"x" * (MAX_AUDIO_BYTES + 1))
    )
    assert big.engine == "CLIENT_FALLBACK"


async def test_tts_endpoint_uses_injected_synthesizer(
    sandbox_app: FastAPI, client: httpx.AsyncClient
) -> None:
    sandbox_app.dependency_overrides[get_tts_synthesizer] = lambda: _FakeSynth()
    r = await client.post(
        "/api/v1/tts/synthesize-vi", json={"text_vi": "xin chào"}, headers=SANDBOX_HEADERS
    )
    assert r.status_code == 200 and r.json()["engine"] == "SERVER_TTS"
