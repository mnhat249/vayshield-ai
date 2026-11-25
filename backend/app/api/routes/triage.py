"""STUB M0 — Cấp cứu Giờ Vàng S9 + TTS tiếng Việt. Logic thật: T15."""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Query, Response

from app.core import mocks
from app.core.auth_headers import DeviceOrSandboxCaller
from app.core.openapi import error_responses
from app.core.schemas.triage import (
    GoldenHourDossierRequest,
    GoldenHourDossierResponse,
    TtsRequest,
    TtsResponse,
)
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1", tags=["triage"])

# PDF 1 trang tối thiểu hợp lệ (mock), không chứa dữ liệu người dùng.
_MOCK_PDF: Final = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

_DOSSIER_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    200: {
        "description": "JSON hồ sơ; hoặc PDF đơn trình báo khi format=pdf",
        "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
    },
    **error_responses(401),
}


@router.post(
    "/triage/golden-hour-dossier",
    operation_id="triageGoldenHourDossier",
    response_model=GoldenHourDossierResponse,
    responses=_DOSSIER_RESPONSES,
    summary="Xuất hotline phong tỏa & đơn trình báo Giờ Vàng (JSON/PDF, < 120ms)",
)
@stub
async def golden_hour_dossier(
    body: GoldenHourDossierRequest,
    _caller: DeviceOrSandboxCaller,
    output_format: Annotated[Literal["json", "pdf"], Query(alias="format")] = "json",
) -> GoldenHourDossierResponse | Response:
    if output_format == "pdf":
        return Response(content=_MOCK_PDF, media_type="application/pdf")
    return mocks.GOLDEN_HOUR


@router.post(
    "/tts/synthesize-vi",
    operation_id="ttsSynthesizeVi",
    response_model=TtsResponse,
    responses=error_responses(401),
    summary="Chuẩn hóa & tổng hợp giọng nói tiếng Việt (fallback cho Web Speech API)",
)
@stub
async def synthesize_vi(body: TtsRequest, _caller: DeviceOrSandboxCaller) -> TtsResponse:
    return mocks.TTS.model_copy(update={"normalized_text_vi": body.text_vi})
