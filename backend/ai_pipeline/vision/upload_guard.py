"""Chốt chặn upload ảnh: magic-byte (EC-DB06) và giới hạn pixel (EC-DB05).

Chỉ nhận JPEG/PNG/WebP. SVG/PDF/HTML bị từ chối vì không khớp magic byte. Ảnh được
tái mã hóa từ ma trận pixel nên không giữ EXIF/metadata/payload polyglot.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_PIXELS = 25_000_000  # EC-DB05: 25MP
MAX_DECODED_BYTES = 128 * 1024 * 1024  # trần bitmap RGBA sau giải nén (25MP*4 = 100MB)


class ImageRejectedError(ValueError):
    """Ảnh bị từ chối; `code` là mã ổn định để map sang lỗi API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ImageHeader:
    fmt: str  # "JPEG" | "PNG" | "WEBP"
    width: int
    height: int


def sniff_format(data: bytes) -> str:
    """Nhận diện định dạng bằng magic byte thực tế, không tin phần mở rộng."""
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    raise ImageRejectedError("UNSUPPORTED_IMAGE_TYPE", "Chỉ chấp nhận ảnh JPEG, PNG hoặc WebP.")


def read_header(data: bytes, max_pixels: int = MAX_PIXELS) -> ImageHeader:
    """Đọc header (không cấp phát bitmap) và kiểm tra giới hạn pixel."""
    fmt = sniff_format(data)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
    except Image.DecompressionBombError as exc:
        raise ImageRejectedError("IMAGE_TOO_LARGE", "Ảnh vượt giới hạn pixel của Pillow.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageRejectedError("CORRUPT_IMAGE", "Ảnh hỏng hoặc không đọc được.") from exc
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ImageRejectedError("IMAGE_TOO_LARGE", "Kích thước ảnh vượt giới hạn 25 megapixel.")
    if width * height * 4 > MAX_DECODED_BYTES:
        raise ImageRejectedError("IMAGE_TOO_LARGE", "Bộ nhớ giải nén vượt giới hạn.")
    return ImageHeader(fmt=fmt, width=width, height=height)


def load_clean_image(data: bytes, max_pixels: int = MAX_PIXELS) -> Image.Image:
    """Giải mã an toàn và trả về ảnh RGBA mới, không còn metadata gốc."""
    read_header(data, max_pixels)  # luôn kiểm tra TRƯỚC khi load bitmap
    try:
        with Image.open(io.BytesIO(data)) as src:
            src.load()
            return src.convert("RGBA")
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageRejectedError("CORRUPT_IMAGE", "Ảnh hỏng hoặc không đọc được.") from exc
