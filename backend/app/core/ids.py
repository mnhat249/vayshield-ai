"""Sinh định danh UUIDv7 (File 4 §2.2: khóa chính UUIDv7, sắp theo thời gian)."""

from __future__ import annotations

import uuid

import uuid6


def new_uuid7() -> uuid.UUID:
    value = uuid6.uuid7()
    return uuid.UUID(str(value))
