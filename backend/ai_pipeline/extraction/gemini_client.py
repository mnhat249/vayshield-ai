"""Interface ``GeminiClient`` + kiểu dữ liệu yêu cầu/kết quả + client giả + pool API key.

Code nghiệp vụ chỉ phụ thuộc ``GeminiClient`` (Protocol). Client thật (``google-genai``) nằm ở
``genai_client.py``; test dùng ``FakeGeminiClient`` — KHÔNG bao giờ gọi mạng (D6).
Không log/ repr API key (``SecretStr``); pool chỉ nhận diện key bằng chỉ số.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, SecretStr

from app.core.clock import Clock


@dataclass(frozen=True, slots=True)
class ImagePart:
    data: bytes
    mime_type: str = "image/jpeg"

    def __repr__(self) -> str:  # không in bytes ảnh ra log
        return f"ImagePart(mime_type={self.mime_type!r}, bytes={len(self.data)})"


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


ContentPart = ImagePart | TextPart


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    model: str
    system_instruction: str
    parts: tuple[ContentPart, ...]
    response_schema: type[BaseModel]
    max_output_tokens: int = 8192  # EC-AI22: dư dả cho schema gọn
    temperature: float = 0.0
    want_logprobs: bool = True


@dataclass(frozen=True, slots=True)
class TokenCandidate:
    token: str
    logprob: float


@dataclass(frozen=True, slots=True)
class TokenLogprob:
    """Token đã chọn + top-k ứng viên (EC-AI15: đo entropy chữ số)."""

    token: str
    logprob: float
    top: tuple[TokenCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    model: str
    finish_reason: str = "STOP"  # STOP | MAX_TOKENS | SAFETY | ...
    tokens: tuple[TokenLogprob, ...] = ()


class GeminiError(Exception):
    """Lỗi gọi model; lớp con quyết định có thử key khác hay không."""


class GeminiQuotaError(GeminiError):
    """HTTP 429 / RESOURCE_EXHAUSTED — key tạm cạn quota, thử key kế."""


class GeminiAuthError(GeminiError):
    """HTTP 401/403 — key hỏng/thu hồi, loại khỏi pool."""


class GeminiTransientError(GeminiError):
    """HTTP 5xx / đứt kết nối — thử key/endpoint kế."""


class GeminiBadRequestError(GeminiError):
    """HTTP 400 — yêu cầu sai; đổi key vô ích, chuyển model kế."""


class NoApiKeyAvailableError(GeminiError):
    """Không còn key nào dùng được."""


class GeminiClient(Protocol):
    async def generate(
        self, request: GenerationRequest, *, api_key: SecretStr
    ) -> GenerationResult: ...


# --- Pool API key xoay vòng ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApiKeyLease:
    index: int
    key: SecretStr


class ApiKeyPool:
    """Xoay vòng round-robin; key cạn quota nghỉ ``quota_cooldown_s``; key hỏng bị loại."""

    def __init__(
        self, keys: Sequence[SecretStr], clock: Clock, *, quota_cooldown_s: float = 60.0
    ) -> None:
        self._keys = list(keys)
        self._clock = clock
        self._cooldown = quota_cooldown_s
        self._available_at = [0.0] * len(self._keys)
        self._disabled = [False] * len(self._keys)
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._keys)

    def acquire(self) -> ApiKeyLease:
        now = self._clock.monotonic()
        n = len(self._keys)
        for step in range(n):
            idx = (self._cursor + step) % n
            if not self._disabled[idx] and self._available_at[idx] <= now:
                self._cursor = (idx + 1) % n
                return ApiKeyLease(idx, self._keys[idx])
        raise NoApiKeyAvailableError("không còn Gemini API key khả dụng")

    def report_quota_exhausted(self, lease: ApiKeyLease) -> None:
        self._available_at[lease.index] = self._clock.monotonic() + self._cooldown

    def report_invalid(self, lease: ApiKeyLease) -> None:
        self._disabled[lease.index] = True

    def __repr__(self) -> str:
        return f"ApiKeyPool(size={len(self._keys)})"


# --- Client giả cho test --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakeReply:
    result: GenerationResult | None = None
    error: GeminiError | None = None
    delay_s: float = 0.0
    text: str | None = None  # tiện: chỉ đưa text, model lấy từ request
    finish_reason: str = "STOP"
    tokens: tuple[TokenLogprob, ...] = ()


@dataclass(frozen=True, slots=True)
class FakeCall:
    model: str
    key_index_hint: str  # 4 ký tự cuối key — chỉ dùng trong test, không phải log
    request: GenerationRequest


Handler = Callable[[GenerationRequest, SecretStr], Awaitable[GenerationResult]]


@dataclass
class FakeGeminiClient:
    """Trả lời theo kịch bản từng model (hàng đợi ``FakeReply``) hoặc theo ``handler``."""

    script: Mapping[str, list[FakeReply]] = field(default_factory=dict)
    handler: Handler | None = None
    calls: list[FakeCall] = field(default_factory=list)

    async def generate(self, request: GenerationRequest, *, api_key: SecretStr) -> GenerationResult:
        self.calls.append(FakeCall(request.model, api_key.get_secret_value()[-4:], request))
        if self.handler is not None:
            return await self.handler(request, api_key)
        queue = self.script.get(request.model)
        if not queue:
            raise AssertionError(f"FakeGeminiClient: hết kịch bản cho model {request.model}")
        reply = queue.pop(0)
        if reply.delay_s:
            await asyncio.sleep(reply.delay_s)
        if reply.error is not None:
            raise reply.error
        if reply.result is not None:
            return reply.result
        return GenerationResult(
            text=reply.text or "",
            model=request.model,
            finish_reason=reply.finish_reason,
            tokens=reply.tokens,
        )

    def models_called(self) -> list[str]:
        return [c.model for c in self.calls]


def reply_json(
    payload: BaseModel | Mapping[str, object],
    *,
    delay_s: float = 0.0,
    finish_reason: str = "STOP",
    tokens: tuple[TokenLogprob, ...] = (),
) -> FakeReply:
    """Tạo ``FakeReply`` từ model/dict (test)."""
    text = (
        payload.model_dump_json()
        if isinstance(payload, BaseModel)
        else json.dumps(payload, ensure_ascii=False)
    )
    return FakeReply(text=text, delay_s=delay_s, finish_reason=finish_reason, tokens=tokens)
