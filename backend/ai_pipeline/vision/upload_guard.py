"""Chốt chặn upload ảnh: magic-byte (EC-DB06) và giới hạn pixel (EC-DB05).

Chỉ nhận JPEG/PNG/WebP. SVG/PDF/HTML bị từ chối vì không khớp magic byte. Ảnh được
tái mã hóa từ ma trận pixel nên không giữ EXIF/metadata/payload polyglot.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_PIXELS = 25_000_000  # EC-DB05: trần pixel danh nghĩa (25MP)
MAX_DECODED_BYTES = 64 * 1024 * 1024  # EC-DB05: bitmap RGBA (4 byte/px) sau giải nén <= 64MB
# => giới hạn THỰC TẾ là 64MB/4 = 16.777.216 px (~16,7MP) vì chặt hơn 25MP.
# Trần dung lượng file thô (byte nén) là đơn vị khác với bitmap giải nén; dùng hằng riêng.
# Trần 1,5MB/ảnh theo contract (client nén <=1280px) thuộc tầng API, không áp ở đây.
MAX_RAW_BYTES = 64 * 1024 * 1024
MAX_EFFECTIVE_PIXELS = MAX_DECODED_BYTES // 4

Image.MAX_IMAGE_PIXELS = MAX_PIXELS


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
    if len(data) > MAX_RAW_BYTES:  # file thô quá lớn: từ chối trước khi mở
        raise ImageRejectedError("IMAGE_TOO_LARGE", "Dung lượng file vượt giới hạn.")
    fmt = sniff_format(data)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
    except Image.DecompressionBombError as exc:
        raise ImageRejectedError("IMAGE_TOO_LARGE", "Ảnh vượt giới hạn pixel của Pillow.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ImageRejectedError("CORRUPT_IMAGE", "Ảnh hỏng hoặc không đọc được.") from exc
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ImageRejectedError(
            "IMAGE_TOO_LARGE",
            f"Kích thước ảnh vượt giới hạn {min(max_pixels, MAX_EFFECTIVE_PIXELS):,} pixel.",
        )
    if width * height * 4 > MAX_DECODED_BYTES:
        raise ImageRejectedError(
            "IMAGE_TOO_LARGE",
            "Ảnh vượt giới hạn thực tế 16,7 megapixel (bitmap RGBA giải nén > 64MB).",
        )
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
