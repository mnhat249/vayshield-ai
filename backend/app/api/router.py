"""Gắn sẵn 10 route module (D5: file khóa ở T00 — task sau chỉ sửa nội dung từng module).

Mỗi module tự khai báo ``router`` với prefix đầy đủ (``/api/v1/...`` hoặc ``/ws/v1/...``).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    audits,
    b2b,
    devices,
    family,
    ops,
    policies,
    sandbox,
    shield,
    triage,
    ws,
)

ROUTE_MODULES = (devices, policies, audits, shield, family, ws, triage, b2b, ops, sandbox)

api_router = APIRouter()
for _module in ROUTE_MODULES:
    api_router.include_router(_module.router)
