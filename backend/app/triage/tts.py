"""TTS tiếng Việt (EC-AI26): chuẩn hóa văn bản + (tuỳ chọn) tổng hợp giọng nói phía server.

Không có nhà cung cấp TTS thật được nối ở đây: ``TtsSynthesizer`` là Protocol (test dùng fake).
Không có synthesizer, hoặc synthesizer lỗi/quá chậm/trả âm thanh quá lớn -> ``CLIENT_FALLBACK``
(client dùng Web Speech API với ``normalized_text_vi``). Văn bản không bao giờ được log.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Final, Protocol

from app.core.schemas.triage import TtsRequest, TtsResponse
from app.triage.verbalizer import normalize_vi

MAX_AUDIO_BYTES: Final = 2_000_000
SYNTH_TIMEOUT_S: Final = 5.0


@dataclass(frozen=True, slots=True)
class TtsAudio:
    mime: str
    data: bytes


class TtsSynthesizer(Protocol):
    async def synthesize(self, text_vi: str, voice: str, speaking_rate: float) -> TtsAudio: ...


def get_tts_synthesizer() -> TtsSynthesizer | None:
    """Dependency; mặc định ``None`` (chưa nối nhà cung cấp TTS) -> luôn CLIENT_FALLBACK."""
    return None


async def synthesize_vi(body: TtsRequest, synth: TtsSynthesizer | None) -> TtsResponse:
    normalized = normalize_vi(body.text_vi)
    if synth is not None and normalized:
        audio: TtsAudio | None
        try:
            audio = await asyncio.wait_for(
                synth.synthesize(normalized, body.voice.value, body.speaking_rate), SYNTH_TIMEOUT_S
            )
        except Exception:  # fail-soft: không để lỗi TTS chặn cảnh báo an toàn
            audio = None
        if audio is not None and 0 < len(audio.data) <= MAX_AUDIO_BYTES:
            return TtsResponse(
                normalized_text_vi=normalized,
                engine="SERVER_TTS",
                audio_mime=audio.mime,
                audio_base64=base64.b64encode(audio.data).decode("ascii"),
            )
    return TtsResponse(normalized_text_vi=normalized, engine="CLIENT_FALLBACK")
