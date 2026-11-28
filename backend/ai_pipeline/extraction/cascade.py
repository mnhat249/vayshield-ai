"""``ModelCascade``: Tầng 1 (primary) → Tầng 2 (secondary) — File 2 §3.1, S2-EC46, EC-AI22.

- Pha 1: Gemini Flash. Đến ``soft_timeout_s`` (4.5s) mà chưa xong → gọi ``on_soft_timeout``
  (để caller khởi động OCR dự phòng song song), vẫn tiếp tục chờ. Đến ``failover_timeout_s``
  (6s) → hủy, chuyển sang model dự phòng (Flash-Lite) với ảnh nén ``960px``.
- Mỗi model xoay vòng nhiều API key: 429 → key nghỉ, thử key kế; 401/403 → loại key; 5xx →
  thử key kế; 400 → chuyển model.
- JSON cắt cụt (EC-AI22): thử ngay model kế (không backoff); nếu tất cả thất bại mà bản sửa
  một phần hợp lệ → trả bản sửa kèm ``truncated_repaired=True`` (caller phải ép xác nhận).
- Hết đường → ``ExtractionUnavailableError`` (caller hạ cấp Tầng 3 OCR / Tầng 4 form 3 ô).
"""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from app.core.config import Settings

from .gemini_client import (
    ApiKeyPool,
    ContentPart,
    GeminiAuthError,
    GeminiBadRequestError,
    GeminiClient,
    GeminiError,
    GeminiQuotaError,
    GenerationRequest,
    GenerationResult,
    ImagePart,
    NoApiKeyAvailableError,
)
from .json_repair import parse_json_object
from .schemas import MultiImageLoanExtraction

logger = logging.getLogger(__name__)

SECONDARY_MAX_SIDE_PX = 960  # File 2 §3.1 Tầng 2

AttemptOutcome = Literal[
    "ok", "timeout", "quota", "auth", "transient", "bad_request", "truncated", "invalid", "no_key"
]


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    model: str
    outcome: AttemptOutcome


@dataclass(frozen=True, slots=True)
class CascadeConfig:
    primary_model: str = "gemini-2.5-flash"
    secondary_model: str = "gemini-2.5-flash-lite"
    escalation_model: str = "gemini-2.5-pro"
    soft_timeout_s: float = 4.5  # S2-EC46
    failover_timeout_s: float = 6.0  # File 2 §3.1
    secondary_timeout_s: float = 6.0
    escalation_timeout_s: float = 6.0  # giả định: không có biến riêng, dùng failover
    max_output_tokens: int = 8192

    @classmethod
    def from_settings(cls, settings: Settings) -> CascadeConfig:
        return cls(
            primary_model=settings.gemini_model_primary,
            secondary_model=settings.gemini_model_secondary,
            escalation_model=settings.gemini_model_escalation,
            soft_timeout_s=settings.gemini_soft_timeout_s,
            failover_timeout_s=settings.gemini_failover_timeout_s,
            secondary_timeout_s=settings.gemini_failover_timeout_s,
            escalation_timeout_s=settings.gemini_failover_timeout_s,
        )


@dataclass(frozen=True, slots=True)
class CascadeResult:
    extraction: MultiImageLoanExtraction
    raw: GenerationResult
    model_used: str
    attempts: tuple[ModelAttempt, ...]
    soft_timeout_fired: bool = False
    truncated_repaired: bool = False


class ExtractionUnavailableError(Exception):
    def __init__(self, attempts: Sequence[ModelAttempt]) -> None:
        super().__init__("không model nào trả được trích xuất hợp lệ")
        self.attempts = tuple(attempts)


def downscale_image_part(part: ImagePart, max_side: int = SECONDARY_MAX_SIDE_PX) -> ImagePart:
    """Nén ảnh cho model dự phòng; ảnh hỏng/không đọc được → giữ nguyên."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(part.data)) as img:
            if max(img.size) <= max_side:
                return part
            img.thumbnail((max_side, max_side))
            out = io.BytesIO()
            img.convert("RGB").save(out, format="JPEG", quality=85)
    except (UnidentifiedImageError, OSError, ValueError):
        return part
    return ImagePart(out.getvalue(), "image/jpeg")


@dataclass
class _ModelRun:
    attempts: list[ModelAttempt] = field(default_factory=list)
    soft_fired: bool = False
    result: GenerationResult | None = None
    extraction: MultiImageLoanExtraction | None = None
    repaired: MultiImageLoanExtraction | None = None
    repaired_raw: GenerationResult | None = None


class ModelCascade:
    def __init__(
        self,
        client: GeminiClient,
        key_pool: ApiKeyPool,
        config: CascadeConfig | None = None,
        *,
        downscaler: Callable[[ImagePart], ImagePart] = downscale_image_part,
    ) -> None:
        self._client = client
        self._keys = key_pool
        self.config = config or CascadeConfig()
        self._downscale = downscaler

    async def extract(
        self,
        *,
        system_instruction: str,
        parts: Sequence[ContentPart],
        on_soft_timeout: Callable[[], None] | None = None,
    ) -> CascadeResult:
        cfg = self.config
        secondary_parts = tuple(
            self._downscale(p) if isinstance(p, ImagePart) else p for p in parts
        )
        plan = [
            (cfg.primary_model, tuple(parts), cfg.failover_timeout_s, cfg.soft_timeout_s),
            (cfg.secondary_model, secondary_parts, cfg.secondary_timeout_s, None),
        ]
        return await self._run_plan(system_instruction, plan, on_soft_timeout)

    async def escalate(
        self, *, system_instruction: str, parts: Sequence[ContentPart]
    ) -> CascadeResult:
        cfg = self.config
        plan = [(cfg.escalation_model, tuple(parts), cfg.escalation_timeout_s, None)]
        return await self._run_plan(system_instruction, plan, None)

    async def _run_plan(
        self,
        system_instruction: str,
        plan: Sequence[tuple[str, tuple[ContentPart, ...], float, float | None]],
        on_soft_timeout: Callable[[], None] | None,
    ) -> CascadeResult:
        attempts: list[ModelAttempt] = []
        soft_fired = False
        fallback: tuple[MultiImageLoanExtraction, GenerationResult] | None = None
        for model, parts, timeout, soft in plan:
            request = GenerationRequest(
                model=model,
                system_instruction=system_instruction,
                parts=parts,
                response_schema=MultiImageLoanExtraction,
                max_output_tokens=self.config.max_output_tokens,
            )
            run = await self._run_model(request, timeout, soft, on_soft_timeout)
            attempts.extend(run.attempts)
            soft_fired = soft_fired or run.soft_fired
            if run.extraction is not None and run.result is not None:
                return CascadeResult(run.extraction, run.result, model, tuple(attempts), soft_fired)
            if fallback is None and run.repaired is not None and run.repaired_raw is not None:
                fallback = (run.repaired, run.repaired_raw)
        if fallback is not None:
            return CascadeResult(
                fallback[0],
                fallback[1],
                fallback[1].model,
                tuple(attempts),
                soft_fired,
                truncated_repaired=True,
            )
        raise ExtractionUnavailableError(attempts)

    async def _run_model(
        self,
        request: GenerationRequest,
        budget_s: float,
        soft: float | None,
        on_soft_timeout: Callable[[], None] | None,
    ) -> _ModelRun:
        run = _ModelRun()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget_s
        soft_deadline = loop.time() + soft if soft is not None else None
        for _ in range(max(1, len(self._keys))):
            remaining = deadline - loop.time()
            if remaining <= 0:
                run.attempts.append(ModelAttempt(request.model, "timeout"))
                return run
            try:
                lease = self._keys.acquire(request.model)
            except NoApiKeyAvailableError:
                run.attempts.append(ModelAttempt(request.model, "no_key"))
                return run
            task = asyncio.ensure_future(self._client.generate(request, api_key=lease.key))
            try:
                if soft_deadline is not None and not run.soft_fired:
                    done, _ = await asyncio.wait(
                        {task}, timeout=max(0.0, soft_deadline - loop.time())
                    )
                    if not done:
                        run.soft_fired = True
                        logger.warning("gemini soft-timeout", extra={"model": request.model})
                        if on_soft_timeout is not None:
                            on_soft_timeout()
                done, _ = await asyncio.wait({task}, timeout=max(0.0, deadline - loop.time()))
                if not done:
                    task.cancel()
                    run.attempts.append(ModelAttempt(request.model, "timeout"))
                    return run  # nghẽn mạng: đổi key vô ích, sang model kế
                result = task.result()
            except GeminiQuotaError:
                self._keys.report_quota_exhausted(lease)
                run.attempts.append(ModelAttempt(request.model, "quota"))
                continue
            except GeminiAuthError:
                self._keys.report_invalid(lease)
                run.attempts.append(ModelAttempt(request.model, "auth"))
                continue
            except GeminiBadRequestError:
                run.attempts.append(ModelAttempt(request.model, "bad_request"))
                return run
            except GeminiError:
                run.attempts.append(ModelAttempt(request.model, "transient"))
                continue
            except Exception:  # lỗi client không lường trước (httpx, SDK) → coi là tạm thời
                logger.warning("gemini unexpected error", extra={"model": request.model})
                run.attempts.append(ModelAttempt(request.model, "transient"))
                continue
            finally:
                if not task.done():
                    task.cancel()
            self._parse_into(run, request.model, result)
            return run
        return run

    @staticmethod
    def _parse_into(run: _ModelRun, model: str, result: GenerationResult) -> None:
        parsed = parse_json_object(result.text)
        truncated = result.finish_reason == "MAX_TOKENS" or (parsed is not None and parsed.repaired)
        if parsed is None:
            run.attempts.append(ModelAttempt(model, "truncated" if truncated else "invalid"))
            return
        try:
            extraction = MultiImageLoanExtraction.model_validate(parsed.data)
        except ValidationError:
            run.attempts.append(ModelAttempt(model, "truncated" if truncated else "invalid"))
            return
        if truncated:
            run.attempts.append(ModelAttempt(model, "truncated"))
            run.repaired, run.repaired_raw = extraction, result
            return
        run.attempts.append(ModelAttempt(model, "ok"))
        run.result, run.extraction = result, extraction
