"""Gói quy tắc ký Ed25519 (File 4 §4.1 < 40ms, §5.1). Logic: T11."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from app.core.errors import AppError, ErrorCode
from app.core.openapi import error_responses
from app.core.schemas.policies import PolicyBundle
from app.services.audit_pipeline.auth import BorrowerCaller
from app.services.audit_pipeline.container import Audit
from app.services.audit_pipeline.policy import build_signed_policy, etag_matches

router = APIRouter(prefix="/api/v1/policies", tags=["edge-policy"])

CACHE_CONTROL = "private, max-age=0, must-revalidate"


@router.get(
    "/latest",
    operation_id="policiesLatest",
    response_model=PolicyBundle,
    responses=error_responses(304, 401),
    summary="Đồng bộ gói quy tắc ký số Ed25519 (ETag, < 40ms)",
)
async def latest_policy(
    response: Response,
    _who: BorrowerCaller,
    services: Audit,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> PolicyBundle | Response:
    await services.ensure_ready()
    if services.signer is None:  # không ký được = không phát hành gói (fail-closed)
        raise AppError(ErrorCode.SERVICE_DEGRADED, 503)
    bundle, etag = build_signed_policy(
        services.registry, services.trust, services.signer, services.clock
    )
    if etag_matches(if_none_match, etag):
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": etag, "Cache-Control": CACHE_CONTROL},
        )
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = CACHE_CONTROL
    return bundle
