"""STUB M0 — Gói quy tắc ký Ed25519. Logic thật: T11 (giữ nguyên path/schema)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from app.core import mocks
from app.core.auth_headers import DeviceOrSandboxCaller
from app.core.openapi import error_responses
from app.core.schemas.policies import PolicyBundle
from app.core.stubs import stub

router = APIRouter(prefix="/api/v1/policies", tags=["edge-policy"])

_MOCK_ETAG = '"policy-v1"'


@router.get(
    "/latest",
    operation_id="policiesLatest",
    response_model=PolicyBundle,
    responses=error_responses(304, 401),
    summary="Đồng bộ gói quy tắc ký số Ed25519 (ETag, < 40ms)",
)
@stub
async def latest_policy(
    response: Response,
    _caller: DeviceOrSandboxCaller,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> PolicyBundle | Response:
    if if_none_match == _MOCK_ETAG:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": _MOCK_ETAG})
    response.headers["ETag"] = _MOCK_ETAG
    response.headers["Cache-Control"] = "private, max-age=0, must-revalidate"
    return mocks.POLICY_BUNDLE
