"""Tiện ích test Trạm 1 & 2: ảnh tổng hợp, ký multipart, đọc SSE (không PII, không mạng)."""

from __future__ import annotations

import io
import json
from typing import Any
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator
from PIL import Image, ImageDraw, ImageFilter

from app.core.clock import FakeClock
from tests.ai_pipeline.vision.helpers import build_vietqr, font, render_qr, to_bytes
from tests.api.shield._helpers import DEVICE_A, H, sign
from tests.contract._helpers import load_json, validator_for_def

STREAM = "/api/v1/audits/contract-stream"
VOICE = "/api/v1/audits/voice-terms"
APK = "/api/v1/audits/apk-verify"


def sharp_image(width: int = 900, height: int = 700) -> Image.Image:
    """Ảnh nhiều chữ tương phản cao: phương sai Laplacian >> ngưỡng mờ."""
    img = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    f = font(26)
    for row in range(0, height - 40, 44):
        d.text(
            (20, row + 8),
            f"Hop dong vay so {row:04d} phi ho so 1.500.000 dong",
            fill=(0, 0, 0),
            font=f,
        )
    return img


def blurry_image() -> Image.Image:
    return sharp_image().filter(ImageFilter.GaussianBlur(radius=12))


def jpeg(img: Image.Image | None = None) -> bytes:
    return to_bytes(img or sharp_image(), "JPEG")


def qr_chat_image(payload: str) -> Image.Image:
    """Ảnh chat: QR trong bong bóng TRẮNG bên trái (= của đối phương → phải kiểm tra)."""
    from tests.ai_pipeline.vision.helpers import chat_image

    return chat_image(render_qr(payload), None)


def vietqr_payload(account: str = "1903123498899", amount: int = 2_000_000) -> str:
    return build_vietqr("970407", account, amount, "PHI MO BANG")


def image_part(data: bytes, name: str = "a.jpg", mime: str = "image/jpeg") -> tuple[str, Any]:
    return ("images", (name, io.BytesIO(data), mime))


async def signed_multipart(
    client: httpx.AsyncClient,
    clock: FakeClock,
    path: str,
    *,
    files: list[Any] | None = None,
    data: dict[str, str] | None = None,
    device: UUID = DEVICE_A,
) -> httpx.Response:
    request = client.build_request("POST", path, files=files, data=data)
    body = request.read()
    request.headers[H] = sign(clock, device, "POST", path, body)[H]
    return await client.send(request)


def meta(**over: Any) -> str:
    body: dict[str, Any] = {"capture_source": "SCREENSHOT"}
    body.update(over)
    return json.dumps(body)


def parse_sse(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        frames.append({"event": lines["event"], "data": json.loads(lines["data"])})
    return frames


def stream_validator() -> Draft202012Validator:
    return validator_for_def(load_json("sse-events.schema.json"), "ContractAuditStream")


def phases(frames: list[dict[str, Any]]) -> list[str]:
    return [f["event"] for f in frames if f["event"] != "ping"]
