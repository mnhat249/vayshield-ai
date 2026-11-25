"""STUB M0 — Trạm 1 & Trạm 2. Logic thật: T11 (giữ nguyên path/operationId/schema).

contract-stream trả SSE 5 pha theo ``contracts/sse-events.schema.json`` với header chống
buffering (File 6 §1.2) và ping keepalive (EC-DB22).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.core import mocks
from app.core.auth_headers import DeviceCaller, DeviceOrSandboxCaller
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode, ErrorDetail
from app.core.openapi import DEGRADABLE, error_responses
from app.core.schemas.audits import (
    ApkVerifyRequest,
    ApkVerifyResponse,
    ContractStreamMetadata,
    RecalculateRequest,
    RecalculateResponse,
    VoiceTermsMetadata,
    VoiceTermsResponse,
)
from app.core.sse import SSE_HEADERS, SSE_MEDIA_TYPE, format_sse, with_keepalive
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/audits", tags=["audits"])

# File 4 §4.2(A): chỉ image/webp hoặc image/jpeg (client đã nén WebP ≤ 1280px).
ALLOWED_IMAGE_TYPES: Final = frozenset({"image/jpeg", "image/webp"})
ALLOWED_AUDIO_TYPES: Final = frozenset(
    {"audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav"}
)

_SSE_RESPONSE_DOC: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": (
            "Luồng SSE: audit.sanitized → audit.vietqr_decoded → audit.fields_extracted → "
            "audit.verdict_computed → audit.legal_explained; ping mỗi 2.5s; audit.error (nếu có) "
            "luôn là event cuối. Schema: contracts/sse-events.schema.json."
        ),
        "content": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "x-sse-schema": "contracts/sse-events.schema.json#/$defs/ContractAuditStream",
            }
        },
        "headers": {
            "X-Accel-Buffering": {"schema": {"type": "string", "enum": ["no"]}},
            "Cache-Control": {"schema": {"type": "string", "enum": ["no-cache, no-transform"]}},
        },
    }
}


def _parse_metadata[M: (ContractStreamMetadata, VoiceTermsMetadata)](raw: str, model: type[M]) -> M:
    try:
        return model.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[ErrorDetail(field="body.metadata_json", issue="invalid_json_or_schema")],
        ) from exc


def _check_upload(file: UploadFile, allowed: frozenset[str], max_bytes: int, field: str) -> None:
    if (file.content_type or "").lower() not in allowed:
        raise AppError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            415,
            details=[ErrorDetail(field=field, issue="unsupported_media_type")],
        )
    if file.size is not None and file.size > max_bytes:
        raise AppError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            413,
            details=[ErrorDetail(field=field, issue="file_too_large")],
        )


@router.post(
    "/apk-verify",
    operation_id="auditsApkVerify",
    response_model=ApkVerifyResponse,
    responses=error_responses(401, *DEGRADABLE),
    summary="Trạm 1: kiểm định chữ ký APK & quyền nguy hiểm (< 90ms)",
)
@stub
async def apk_verify(body: ApkVerifyRequest, _caller: DeviceCaller) -> ApkVerifyResponse:
    return mocks.APK_VERIFY


@router.post(
    "/contract-stream",
    operation_id="auditsContractStream",
    response_class=StreamingResponse,
    responses={**_SSE_RESPONSE_DOC, **error_responses(401, 413, 415, *DEGRADABLE)},
    summary="Trạm 2: soi 1–4 ảnh hợp đồng → SSE 5 pha (first event < 200ms, full < 3.5s)",
)
@stub
async def contract_stream(
    _caller: DeviceOrSandboxCaller,
    images: Annotated[list[UploadFile], File(description="1–4 ảnh webp/jpeg, ≤ 1.5MB/ảnh")],
    metadata_json: Annotated[str, Form(description="JSON theo ContractStreamMetadata")],
) -> StreamingResponse:
    settings = get_settings()
    if not 1 <= len(images) <= settings.upload_max_images:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[ErrorDetail(field="body.images", issue="image_count_out_of_range")],
        )
    for idx, image in enumerate(images):
        _check_upload(
            image, ALLOWED_IMAGE_TYPES, settings.upload_max_image_bytes, f"body.images.{idx}"
        )
    _parse_metadata(metadata_json, ContractStreamMetadata)
    image_count = len(images)

    async def events() -> AsyncIterator[str]:
        for name, payload in mocks.CONTRACT_STREAM_EVENTS:
            if name == "audit.sanitized":
                payload = payload.model_copy(update={"image_count": image_count})
            yield format_sse(name, payload)

    return StreamingResponse(
        with_keepalive(events(), settings.sse_keepalive_interval_s),
        media_type=f"{SSE_MEDIA_TYPE}; charset=utf-8",
        headers=dict(SSE_HEADERS),
    )


@router.post(
    "/voice-terms",
    operation_id="auditsVoiceTerms",
    response_model=VoiceTermsResponse,
    responses=error_responses(401, 413, 415, *DEGRADABLE),
    summary="Trạm 2: đọc miệng điều khoản khi app vay chặn chụp màn hình (< 1.8s)",
)
@stub
async def voice_terms(
    _caller: DeviceOrSandboxCaller,
    metadata_json: Annotated[str, Form(description="JSON theo VoiceTermsMetadata")],
    audio: Annotated[UploadFile | None, File(description="Âm thanh ≤ 5MB")] = None,
    transcript_text: Annotated[
        str | None, Form(max_length=2000, description="Bản ghi STT trên máy (nếu có)")
    ] = None,
) -> VoiceTermsResponse:
    settings = get_settings()
    _parse_metadata(metadata_json, VoiceTermsMetadata)
    if audio is None and not transcript_text:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[ErrorDetail(field="body", issue="audio_or_transcript_required")],
        )
    if audio is not None:
        _check_upload(audio, ALLOWED_AUDIO_TYPES, settings.upload_max_audio_bytes, "body.audio")
    return mocks.VOICE_TERMS


@router.post(
    "/{audit_id}/recalculate",
    operation_id="auditsRecalculate",
    response_model=RecalculateResponse,
    responses=error_responses(401, 404, *DEGRADABLE),
    summary="Trạm 2: giải lại dòng tiền tất định khi người dùng sửa số (< 15ms, 0 LLM)",
)
@stub
async def recalculate(
    audit_id: UUID, body: RecalculateRequest, _caller: DeviceOrSandboxCaller
) -> RecalculateResponse:
    return mocks.RECALCULATE.model_copy(update={"audit_id": audit_id})
