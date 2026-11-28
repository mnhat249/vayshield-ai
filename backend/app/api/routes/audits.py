"""Trạm 1 & Trạm 2 (File 4 §4.1, §4.2(A)). Logic: T11 (path/operationId/schema theo contract).

contract-stream trả SSE 5 pha theo ``contracts/sse-events.schema.json`` với header chống
buffering (File 6 §1.2) và ping keepalive (EC-DB22). Route KHÔNG dùng ``Depends(get_db)``
(EC-DB01): kết nối DB chỉ được mượn ngắn trong repository.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import File, Form, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter
from pydantic import ValidationError

from app.core.errors import AppError, ErrorCode, ErrorDetail
from app.core.ids import new_uuid7
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
from app.core.sse import SSE_HEADERS, SSE_MEDIA_TYPE, with_keepalive
from app.services.audit_pipeline import service
from app.services.audit_pipeline.auth import (
    BodyCachingRoute,
    BorrowerCaller,
    DeferredAuth,
    DevicePrincipal,
)
from app.services.audit_pipeline.container import Audit
from app.services.audit_pipeline.ingest import ingest_images, read_uploads
from app.services.audit_pipeline.stream import StreamJob, contract_events

router = APIRouter(prefix="/api/v1/audits", tags=["audits"], route_class=BodyCachingRoute)

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


@router.post(
    "/apk-verify",
    operation_id="auditsApkVerify",
    response_model=ApkVerifyResponse,
    responses=error_responses(401, *DEGRADABLE),
    summary="Trạm 1: kiểm định chữ ký APK & quyền nguy hiểm (< 90ms)",
)
async def apk_verify(
    body: ApkVerifyRequest, who: DevicePrincipal, services: Audit
) -> ApkVerifyResponse:
    return await service.apk_verify(services, who, body)


@router.post(
    "/contract-stream",
    operation_id="auditsContractStream",
    response_class=StreamingResponse,
    responses={**_SSE_RESPONSE_DOC, **error_responses(401, 413, 415, *DEGRADABLE)},
    summary="Trạm 2: soi 1–4 ảnh hợp đồng → SSE 5 pha (first event < 200ms, full < 3.5s)",
)
async def contract_stream(
    authenticate: DeferredAuth,
    services: Audit,
    images: Annotated[list[UploadFile], File(description="1–4 ảnh webp/jpeg, ≤ 1.5MB/ảnh")],
    metadata_json: Annotated[str, Form(description="JSON theo ContractStreamMetadata")],
) -> StreamingResponse:
    started = services.clock.monotonic()
    settings = services.settings
    if not 1 <= len(images) <= settings.upload_max_images:
        for upload in images:
            await upload.close()
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[ErrorDetail(field="body.images", issue="image_count_out_of_range")],
        )
    for idx, upload in enumerate(images):  # kiểm loại tệp trước khi tốn công verify chữ ký
        if (upload.content_type or "").lower() not in ALLOWED_IMAGE_TYPES:
            for u in images:
                await u.close()
            raise AppError(
                ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                415,
                details=[ErrorDetail(field=f"body.images.{idx}", issue="unsupported_media_type")],
            )
    meta = _parse_metadata(metadata_json, ContractStreamMetadata)
    who = await authenticate()
    await services.ensure_ready()
    raw = await read_uploads(
        images,
        allowed=ALLOWED_IMAGE_TYPES,
        max_bytes=settings.upload_max_image_bytes,
        field="body.images",
    )
    # Chặng 1 xong TRƯỚC khi mở luồng: ảnh hỏng/quá cỡ → lỗi HTTP JSON chuẩn (EC-DB05/06),
    # còn ``audit.sanitized`` là event đầu tiên ngay khi luồng mở (< 200ms).
    ingest = await ingest_images(raw, services.cpu)
    wage = await service.wage_for(services, who)
    job = StreamJob(
        audit_id=new_uuid7(),
        principal=who,
        meta=meta,
        ingest=ingest,
        wage_vnd=wage,
        elapsed_ms_at_start=int((services.clock.monotonic() - started) * 1000),
        started=started,
    )
    return StreamingResponse(
        with_keepalive(contract_events(services, job), settings.sse_keepalive_interval_s),
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
async def voice_terms(
    authenticate: DeferredAuth,
    services: Audit,
    metadata_json: Annotated[str, Form(description="JSON theo VoiceTermsMetadata")],
    audio: Annotated[UploadFile | None, File(description="Âm thanh ≤ 5MB")] = None,
    transcript_text: Annotated[
        str | None, Form(max_length=2000, description="Bản ghi STT trên máy (nếu có)")
    ] = None,
) -> VoiceTermsResponse:
    meta = _parse_metadata(metadata_json, VoiceTermsMetadata)
    if audio is None and not transcript_text:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            422,
            details=[ErrorDetail(field="body", issue="audio_or_transcript_required")],
        )
    if audio is not None and (audio.content_type or "").lower() not in ALLOWED_AUDIO_TYPES:
        await audio.close()
        raise AppError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            415,
            details=[ErrorDetail(field="body.audio", issue="unsupported_media_type")],
        )
    who = await authenticate()
    audio_part: tuple[bytes, str] | None = None
    if audio is not None:
        mime = (audio.content_type or "").lower()
        (data,) = await read_uploads(
            [audio],
            allowed=ALLOWED_AUDIO_TYPES,
            max_bytes=services.settings.upload_max_audio_bytes,
            field="body.audio",
            indexed=False,
        )
        audio_part = (data, mime)
    return await service.voice_terms(
        services, who, meta, audio=audio_part, transcript_text=transcript_text
    )


@router.post(
    "/{audit_id}/recalculate",
    operation_id="auditsRecalculate",
    response_model=RecalculateResponse,
    responses=error_responses(401, 404, *DEGRADABLE),
    summary="Trạm 2: giải lại dòng tiền tất định khi người dùng sửa số (< 15ms, 0 LLM)",
)
async def recalculate(
    audit_id: UUID, body: RecalculateRequest, who: BorrowerCaller, services: Audit
) -> RecalculateResponse:
    return await service.recalculate(services, who, audit_id, body)
