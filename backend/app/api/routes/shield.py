"""STUB M0 — Trạm 3: Khiên 24h, đánh giá 10 trục, leo thang. Logic thật: T12."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core import mocks
from app.core.auth_headers import DeviceOrSandboxCaller
from app.core.openapi import DEGRADABLE, error_responses
from app.core.schemas.shield import (
    EscalateRequest,
    EscalateResponse,
    EvaluateResponseRequest,
    EvaluateResponseResult,
    ShieldArmRequest,
    ShieldArmResponse,
)
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/shield", tags=["shield"])


@router.post(
    "/arm",
    operation_id="shieldArm",
    response_model=ShieldArmResponse,
    responses=error_responses(401, 409),
    summary="Đồng bộ kích hoạt Khiên 24h (< 60ms)",
)
@stub
async def arm_shield(body: ShieldArmRequest, _caller: DeviceOrSandboxCaller) -> ShieldArmResponse:
    return mocks.SHIELD_ARM.model_copy(update={"fiber_key": body.fiber_key})


@router.post(
    "/evaluate-response",
    operation_id="shieldEvaluateResponse",
    response_model=EvaluateResponseResult,
    responses=error_responses(401, 404, *DEGRADABLE),
    summary="Phân tích câu trả lời theo 10 trục ontology & chống mớm lời (< 900ms)",
)
@stub
async def evaluate_response(
    body: EvaluateResponseRequest, _caller: DeviceOrSandboxCaller
) -> EvaluateResponseResult:
    return mocks.EVALUATE_RESPONSE.model_copy(update={"session_id": body.session_id})


@router.post(
    "/escalate",
    operation_id="shieldEscalate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=EscalateResponse,
    responses=error_responses(401, 404, 409, *DEGRADABLE),
    summary="Leo thang WS → Push → SMS tới người thân (Zero-Amount, WS < 250ms)",
)
@stub
async def escalate(body: EscalateRequest, _caller: DeviceOrSandboxCaller) -> EscalateResponse:
    return mocks.ESCALATE
