"""T03 - Tiền xử lý thị giác đối kháng + giải mã VietQR (WP1-04)."""

from ai_pipeline.vision.concurrency import run_cpu_bound
from ai_pipeline.vision.forensics import ReceiptForensics, receipt_forensics
from ai_pipeline.vision.scrub import SanitizedImage, StampResult, sanitize_image, strip_red_stamp
from ai_pipeline.vision.text import canonicalize_text
from ai_pipeline.vision.tiling import Tile, tile_long_screenshot
from ai_pipeline.vision.upload_guard import ImageRejectedError
from ai_pipeline.vision.vietqr import (
    AttributedQr,
    QrSender,
    UntrustedQrPayload,
    VietQrError,
    VietQrPayload,
    attribute_qr_sender,
    decode_vietqr,
    detect_qr_codes,
)

__all__ = [
    "AttributedQr",
    "ImageRejectedError",
    "QrSender",
    "ReceiptForensics",
    "SanitizedImage",
    "StampResult",
    "Tile",
    "UntrustedQrPayload",
    "VietQrError",
    "VietQrPayload",
    "attribute_qr_sender",
    "canonicalize_text",
    "decode_vietqr",
    "detect_qr_codes",
    "receipt_forensics",
    "run_cpu_bound",
    "sanitize_image",
    "strip_red_stamp",
    "tile_long_screenshot",
]
