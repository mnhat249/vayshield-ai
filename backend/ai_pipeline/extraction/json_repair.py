"""Sửa JSON bị cắt cụt (EC-AI22: ``MaxTokens`` hoặc đứt luồng mạng).

Nguyên tắc an toàn: chỉ cắt về điểm mà giá trị trước đó CHẮC CHẮN đã đầy đủ (ngay trước dấu
``,`` hoặc ngay sau ``}``/``]``) rồi đóng ngoặc. Không bao giờ giữ một số đang dở dang
(``"amount_vnd": 105`` có thể là ``1050000`` bị cắt) — cắt cụt không được làm đổi số.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class JsonParse:
    data: dict[str, Any]
    repaired: bool


def strip_code_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _cut_points(text: str) -> list[tuple[int, list[str]]]:
    """Danh sách (vị trí cắt, ngăn xếp ngoặc đang mở tại đó) theo thứ tự tăng dần."""
    points: list[tuple[int, list[str]]] = []
    stack: list[str] = []
    in_string = False
    escape = False
    for idx, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
            points.append((idx + 1, list(stack)))
        elif ch in "}]":
            if not stack:
                break
            stack.pop()
            points.append((idx + 1, list(stack)))
        elif ch == "," and stack:
            points.append((idx, list(stack)))
    return points


def parse_json_object(text: str) -> JsonParse | None:
    """Parse JSON object; nếu bị cắt cụt thì sửa an toàn. ``None`` nếu không cứu được."""
    cleaned = strip_code_fences(text)
    start = cleaned.find("{")
    if start < 0:
        return None
    cleaned = cleaned[start:]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    else:
        return JsonParse(value, repaired=False) if isinstance(value, dict) else None
    for pos, stack in reversed(_cut_points(cleaned)):
        candidate = cleaned[:pos].rstrip()
        if candidate.endswith(","):
            candidate = candidate[:-1]
        candidate += "".join(reversed(stack))
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return JsonParse(value, repaired=True)
    return None
