"""Trạm 3: Khiên 24h, đánh giá 10 trục, leo thang (File 4 §4.1, §4.2(C)). Logic: T12."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.openapi import DEGRADABLE, error_responses
from app.core.schemas.shield import (
    EscalateRequest,
    EscalateResponse,
    EvaluateResponseRequest,
    EvaluateResponseResult,
    ShieldArmRequest,
    ShieldArmResponse,
)
from app.services.realtime.container import BorrowerCaller, Services

router = APIRouter(prefix="/api/v1/shield", tags=["shield"])


@router.post(
    "/arm",
    operation_id="shieldArm",
    response_model=ShieldArmResponse,
    responses=error_responses(401, 409),
    summary="Đồng bộ kích hoạt Khiên 24h (< 60ms)",
)
async def arm_shield(
    body: ShieldArmRequest, who: BorrowerCaller, services: Services
) -> ShieldArmResponse:
    return await services.shield.arm(who, body)


@router.post(
    "/evaluate-response",
    operation_id="shieldEvaluateResponse",
    response_model=EvaluateResponseResult,
    responses=error_responses(401, 404, *DEGRADABLE),
    summary="Phân tích câu trả lời theo 10 trục ontology & chống mớm lời (< 900ms)",
)
async def evaluate_response(
    body: EvaluateResponseRequest, who: BorrowerCaller, services: Services
) -> EvaluateResponseResult:
    return await services.shield.evaluate(who, body)


@router.post(
    "/escalate",
    operation_id="shieldEscalate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EscalateResponse,
    responses=error_responses(401, 404, 409, *DEGRADABLE),
    summary="Leo thang WS → Push → SMS tới người thân (Zero-Amount, WS < 250ms)",
)
async def escalate(
    body: EscalateRequest, who: BorrowerCaller, services: Services
) -> EscalateResponse:
    return await services.shield.escalate(who, body)
