"""Healthcheck (File 6 §1.2 mục 1: cron 180s FastAPI → PG → Redis)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ComponentStatus = Literal["ok", "down", "skipped"]


class HealthChecks(BaseModel):
    database: ComponentStatus
    redis: ComponentStatus


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    checks: HealthChecks
