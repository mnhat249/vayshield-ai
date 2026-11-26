"""Client Gemini thật dùng ``google-genai`` (chỉ dùng khi chạy thật; test KHÔNG gọi mạng).

Ánh xạ lỗi HTTP → lớp lỗi của ``gemini_client`` để ``ModelCascade`` xoay key / failover.
Không log nội dung ảnh, prompt hay API key.
"""

from __future__ import annotations

from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import SecretStr

from .gemini_client import (
    GeminiAuthError,
    GeminiBadRequestError,
    GeminiError,
    GeminiQuotaError,
    GeminiTransientError,
    GenerationRequest,
    GenerationResult,
    ImagePart,
    TokenCandidate,
    TokenLogprob,
)

TOP_LOGPROBS = 5


def map_api_error(code: int | None) -> GeminiError:
    if code == 429:
        return GeminiQuotaError("quota")
    if code in (401, 403):
        return GeminiAuthError("auth")
    if code is not None and 400 <= code < 500:
        return GeminiBadRequestError(f"http {code}")
    return GeminiTransientError(f"http {code}")


def _tokens_from_logprobs(result: Any) -> tuple[TokenLogprob, ...]:
    if result is None or not result.chosen_candidates:
        return ()
    tops = result.top_candidates or []
    out: list[TokenLogprob] = []
    for i, chosen in enumerate(result.chosen_candidates):
        top = tops[i].candidates if i < len(tops) and tops[i].candidates else []
        out.append(
            TokenLogprob(
                token=chosen.token or "",
                logprob=chosen.log_probability or 0.0,
                top=tuple(TokenCandidate(c.token or "", c.log_probability or 0.0) for c in top),
            )
        )
    return tuple(out)


class GoogleGenAIClient:
    """Adapter ``GeminiClient`` → ``google.genai`` (async)."""

    async def generate(self, request: GenerationRequest, *, api_key: SecretStr) -> GenerationResult:
        client = genai.Client(api_key=api_key.get_secret_value())
        contents: list[Any] = [
            types.Part.from_bytes(data=p.data, mime_type=p.mime_type)
            if isinstance(p, ImagePart)
            else types.Part.from_text(text=p.text)
            for p in request.parts
        ]
        config = types.GenerateContentConfig(
            system_instruction=request.system_instruction,
            response_mime_type="application/json",
            response_schema=request.response_schema,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
            response_logprobs=request.want_logprobs,
            logprobs=TOP_LOGPROBS if request.want_logprobs else None,
        )
        try:
            response = await client.aio.models.generate_content(
                model=request.model, contents=contents, config=config
            )
        except genai_errors.APIError as exc:
            raise map_api_error(exc.code) from None
        except OSError as exc:
            raise GeminiTransientError("network") from exc
        candidate = response.candidates[0] if response.candidates else None
        finish = (
            candidate.finish_reason.name
            if candidate is not None and candidate.finish_reason is not None
            else "STOP"
        )
        return GenerationResult(
            text=response.text or "",
            model=request.model,
            finish_reason=finish,
            tokens=_tokens_from_logprobs(candidate.logprobs_result if candidate else None),
        )
