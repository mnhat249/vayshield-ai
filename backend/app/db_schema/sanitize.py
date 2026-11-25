"""Làm sạch văn bản/JSON trước mọi lệnh ghi PostgreSQL (EC-DB26, EC-DB18)."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from pydantic import AfterValidator

# Tước 0x00..0x1F trừ \t (0x09) và \n (0x0A).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0B-\x1F]")

MAX_JSON_BYTES = 64 * 1024  # < 64KB
MAX_JSON_DEPTH = 5


def strip_control_chars(text: str) -> str:
    """EC-DB26: bỏ U+0000 và control char (giữ ``\\n``, ``\\t``)."""
    return _CONTROL_RE.sub("", text)


def sanitize_json(value: Any) -> Any:
    """Đệ quy tước control char ở mọi chuỗi (cả khóa) của cấu trúc JSON."""
    if isinstance(value, str):
        return strip_control_chars(value)
    if isinstance(value, list):
        return [sanitize_json(v) for v in value]
    if isinstance(value, dict):
        return {strip_control_chars(str(k)): sanitize_json(v) for k, v in value.items()}
    return value


def json_depth(value: Any) -> int:
    """Độ sâu lồng nhau: container gốc = 1, scalar = 0 (khớp ``jsonb_max_depth`` SQL)."""
    if isinstance(value, dict):
        return 1 + max((json_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((json_depth(v) for v in value), default=0)
    return 0


def validate_masked_extraction(value: dict[str, Any]) -> dict[str, Any]:
    """EC-DB18: từ chối JSON sâu > 5 hoặc nặng >= 64KB; EC-DB26: tước null byte."""
    cleaned = sanitize_json(value)
    if json_depth(cleaned) > MAX_JSON_DEPTH:
        raise ValueError(f"masked_extraction_json sâu quá {MAX_JSON_DEPTH} cấp")
    size = len(json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size >= MAX_JSON_BYTES:
        raise ValueError("masked_extraction_json nặng từ 64KB trở lên")
    return cleaned  # type: ignore[no-any-return]


MaskedExtractionJson = Annotated[dict[str, Any], AfterValidator(validate_masked_extraction)]
