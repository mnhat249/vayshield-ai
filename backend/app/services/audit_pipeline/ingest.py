"""Chặng 1–2 (File 2 §1.3): làm sạch ảnh + quét VietQR. Mọi việc nặng CPU chạy trong threadpool
(EC-OPS12) qua ``CpuPool`` (giới hạn đồng thời để 1 request nặng không bóp nghẹt event loop).

EC-OPS13: ``read_uploads`` luôn đóng ``UploadFile`` (xóa tệp tạm spool) kể cả khi lỗi.
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

import cv2
import numpy as np
from fastapi import UploadFile
from PIL import Image

from ai_pipeline.vision import (
    ImageRejectedError,
    QrSender,
    VietQrError,
    attribute_qr_sender,
    decode_vietqr,
    detect_qr_codes,
    run_cpu_bound,
)
from ai_pipeline.vision.scrub import HIDDEN_SUSPECT_PIXELS, scrub_hidden_text
from ai_pipeline.vision.upload_guard import read_header
from app.core.errors import AppError, ErrorCode, ErrorDetail

# S2-EC20: phương sai Laplacian dưới ngưỡng → ảnh mờ (giả định kinh nghiệm: 100 trên ảnh ≤1280px).
BLUR_VARIANCE_MIN: Final = 100.0
LLM_JPEG_QUALITY: Final = 90
MAX_ANALYSIS_SIDE: Final = 1280


class CpuPool:
    """Giới hạn số tác vụ CPU-bound chạy cùng lúc; mỗi tác vụ đi qua ``asyncio.to_thread``."""

    def __init__(self, workers: int = 4) -> None:
        self._sem = asyncio.Semaphore(workers)

    async def run[R](self, func: Callable[..., R], /, *args: Any) -> R:
        async with self._sem:
            return await run_cpu_bound(func, *args)


async def read_uploads(
    files: Sequence[UploadFile],
    *,
    allowed: frozenset[str],
    max_bytes: int,
    field: str,
    indexed: bool = True,
) -> list[bytes]:
    """Đọc từng tệp (chặn quá cỡ ngay khi đọc, không tin ``size``), rồi LUÔN đóng mọi tệp."""
    out: list[bytes] = []
    try:
        for idx, upload in enumerate(files):
            where = f"{field}.{idx}" if indexed else field
            if (upload.content_type or "").lower() not in allowed:
                raise AppError(
                    ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    415,
                    details=[ErrorDetail(field=where, issue="unsupported_media_type")],
                )
            data = await upload.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise AppError(
                    ErrorCode.PAYLOAD_TOO_LARGE,
                    413,
                    details=[ErrorDetail(field=where, issue="file_too_large")],
                )
            out.append(data)
    finally:
        for upload in files:
            await upload.close()
    return out


def laplacian_variance(image: Image.Image) -> float:
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    if max(gray.shape) > MAX_ANALYSIS_SIDE:
        scale = MAX_ANALYSIS_SIDE / max(gray.shape)
        gray = np.asarray(
            cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA),
            dtype=np.uint8,
        )
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


@dataclass(frozen=True, slots=True)
class CleanImage:
    """Chỉ giữ JPEG đã thu nhỏ (≤ 1280px) — không giữ bitmap đầy đủ suốt luồng."""

    jpeg: bytes  # ảnh đã khử chữ tàng hình, không metadata → đưa cho LLM + quét QR
    blur_variance: float
    hidden_text_suspected: bool


MAX_SIDE_PX: Final = 1280  # File 4 §4.2(A): client nén ≤ 1280px
MAX_IMAGE_PIXELS: Final = 6_000_000  # trần mỗi ảnh (≈ 2450² ); vượt → 413 TRƯỚC khi giải mã
MAX_REQUEST_PIXELS: Final = 16_000_000  # trần tổng pixel khai báo của cả request


def _shrink(raw: bytes) -> Image.Image:
    """Đọc header (guard T03), chặn ảnh quá lớn, thu nhỏ cạnh dài ≤ 1280px, trả bitmap RGB.

    Trần 6MP/ảnh (ví dụ ảnh 12MP bị 413) là có chủ đích: client PHẢI nén ≤ 1280px (File 4
    §4.2(A)); ảnh gốc từ camera cần nén trước khi gửi. Ảnh cắt cụt/hỏng sau header → 422
    ``CORRUPT_IMAGE`` (EC-DB05), không bao giờ 500."""
    header = read_header(raw)
    if header.width * header.height > MAX_IMAGE_PIXELS:
        raise ImageRejectedError("IMAGE_TOO_LARGE", "Ảnh vượt giới hạn pixel của API.")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if header.fmt == "JPEG":
                img.draft("RGB", (MAX_SIDE_PX, MAX_SIDE_PX))  # giải mã giảm tỉ lệ ngay từ đầu
            img.load()
            rgba = img.convert("RGBA")
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageRejectedError("CORRUPT_IMAGE", "Ảnh hỏng hoặc bị cắt cụt.") from exc
    if max(rgba.size) > MAX_SIDE_PX:
        rgba.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.Resampling.LANCZOS)
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def _clean_one(raw: bytes) -> CleanImage:
    rgb = np.ascontiguousarray(np.asarray(_shrink(raw), dtype=np.uint8))
    cleaned, hidden, _ = scrub_hidden_text(rgb)  # khử chữ tàng hình trên bitmap đã thu nhỏ
    image = Image.fromarray(cleaned, mode="RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=LLM_JPEG_QUALITY)
    return CleanImage(
        jpeg=buf.getvalue(),
        blur_variance=laplacian_variance(image),
        hidden_text_suspected=hidden >= HIDDEN_SUSPECT_PIXELS,
    )


def image_error(exc: ImageRejectedError) -> AppError:
    code = exc.code
    if code == "UNSUPPORTED_IMAGE_TYPE":
        return AppError(ErrorCode.UNSUPPORTED_MEDIA_TYPE, 415)
    if code == "IMAGE_TOO_LARGE":
        return AppError(ErrorCode.PAYLOAD_TOO_LARGE, 413)
    return AppError(
        ErrorCode.VALIDATION_ERROR, 422, details=[ErrorDetail(field="body.images", issue=code)]
    )


@dataclass(frozen=True, slots=True)
class IngestResult:
    images: tuple[CleanImage, ...]

    @property
    def blur_ok(self) -> bool:
        return all(i.blur_variance >= BLUR_VARIANCE_MIN for i in self.images)

    @property
    def quality(self) -> float:
        """0..1; đúng ngưỡng mờ = 0.5 = ``IMAGE_QUALITY_FLOOR`` của Conformal router (T04)."""
        worst = min(i.blur_variance for i in self.images)
        return max(0.0, min(1.0, worst / (2 * BLUR_VARIANCE_MIN)))


async def ingest_images(raw_images: Sequence[bytes], pool: CpuPool) -> IngestResult:
    """Chặng 1: magic-byte/pixel guard + khử chữ tàng hình + đo độ mờ, song song trên threadpool."""
    try:
        total = sum(h.width * h.height for h in (read_header(r) for r in raw_images))
        if total > MAX_REQUEST_PIXELS:
            raise ImageRejectedError("IMAGE_TOO_LARGE", "Tổng pixel của request vượt giới hạn.")
        cleaned = await asyncio.gather(*(pool.run(_clean_one, raw) for raw in raw_images))
    except ImageRejectedError as exc:
        raise image_error(exc) from exc
    return IngestResult(tuple(cleaned))


@dataclass(frozen=True, slots=True)
class QrFinding:
    found: bool
    bank_bin: str | None = None
    account_no: str | None = None  # chỉ giữ trong RAM; không bao giờ log/lưu
    amount_vnd: int | None = None
    injection_detected: bool = False


NO_QR: Final = QrFinding(found=False)


def _scan_one(jpeg: bytes) -> QrFinding:
    with Image.open(io.BytesIO(jpeg)) as opened:
        image = opened.convert("RGB")
    detections = detect_qr_codes(image)
    for attributed in attribute_qr_sender(image, detections):
        if not attributed.should_check:  # QR của chính người dùng (EC-AI02)
            continue
        try:
            payload = decode_vietqr(attributed.detection.text)
        except VietQrError:
            continue
        return QrFinding(
            found=True,
            bank_bin=payload.bank_bin,
            account_no=payload.account_number,
            amount_vnd=payload.amount_vnd,
            injection_detected=payload.untrusted.injection_detected,
        )
    return NO_QR


async def scan_qr(images: Sequence[CleanImage], pool: CpuPool) -> QrFinding:
    """Chặng 2: VietQR (EMVCo TLV + CRC16). Lỗi quét không bao giờ làm hỏng audit."""
    results = await asyncio.gather(
        *(pool.run(_scan_one, i.jpeg) for i in images), return_exceptions=True
    )
    for r in results:
        if isinstance(r, QrFinding) and r.found:
            return r
    return NO_QR


__all__ = [
    "BLUR_VARIANCE_MIN",
    "NO_QR",
    "CleanImage",
    "CpuPool",
    "IngestResult",
    "QrFinding",
    "QrSender",
    "image_error",
    "ingest_images",
    "laplacian_variance",
    "read_uploads",
    "scan_qr",
]
