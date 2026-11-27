"""Cấp cứu Giờ Vàng S9 + TTS tiếng Việt (File 2 S2-EC48, File 3 EC-AI26, File 4 §4.1).

Xác thực người vay (chữ ký thiết bị/Sandbox) + rate limit qua ``BorrowerCaller`` (T12/T10).
Phản hồi không bao giờ được cache (chứa thông tin cá nhân hoá) -> ``Cache-Control: no-store``.
"""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.core.clock import Clock, get_clock
from app.core.openapi import error_responses
from app.core.schemas.triage import (
    GoldenHourDossierRequest,
    GoldenHourDossierResponse,
    TtsRequest,
    TtsResponse,
)
from app.services.realtime.container import BorrowerCaller
from app.triage.golden_hour import build_golden_hour_dossier
from app.triage.hotlines import BankHotlineRegistry, get_bank_hotline_registry
from app.triage.pdf import render_report_pdf
from app.triage.tts import TtsSynthesizer, get_tts_synthesizer, synthesize_vi

router = APIRouter(prefix="/api/v1", tags=["triage"])

_NO_STORE: Final = {"Cache-Control": "no-store"}

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
async def golden_hour_dossier(
    body: GoldenHourDossierRequest,
    _who: BorrowerCaller,
    registry: Annotated[BankHotlineRegistry, Depends(get_bank_hotline_registry)],
    clock: Annotated[Clock, Depends(get_clock)],
    response: Response,
    output_format: Annotated[Literal["json", "pdf"], Query(alias="format")] = "json",
) -> GoldenHourDossierResponse | Response:
    dossier = build_golden_hour_dossier(body, registry, clock.now())
    if output_format == "pdf":
        return Response(
            content=render_report_pdf(dossier), media_type="application/pdf", headers=_NO_STORE
        )
    response.headers.update(_NO_STORE)
    return dossier


@router.post(
    "/tts/synthesize-vi",
    operation_id="ttsSynthesizeVi",
    response_model=TtsResponse,
    responses=error_responses(401),
    summary="Chuẩn hóa & tổng hợp giọng nói tiếng Việt (fallback cho Web Speech API)",
)
async def synthesize_vi_route(
    body: TtsRequest,
    _who: BorrowerCaller,
    synth: Annotated[TtsSynthesizer | None, Depends(get_tts_synthesizer)],
) -> TtsResponse:
    return await synthesize_vi(body, synth)
