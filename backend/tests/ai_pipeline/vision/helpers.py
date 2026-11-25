"""Fixture ảnh/QR tổng hợp sinh bằng code (không dùng ảnh thật, không PII)."""

from __future__ import annotations

import io
import struct
import zlib

import numpy as np
import zxingcpp
from PIL import Image, ImageDraw, ImageFont

from ai_pipeline.vision.vietqr import crc16_ccitt


def tlv(tag: str, value: str) -> str:
    return f"{tag}{len(value):02d}{value}"


def build_vietqr(
    bank_bin: str = "970436",
    account: str = "0011001234567",
    amount: int | None = 1_500_000,
    memo: str = "TIEN VAY",
    *,
    extra_62: str = "",
) -> str:
    """Dựng chuỗi VietQR hợp lệ (có CRC16) theo EMVCo/NAPAS."""
    t38 = tlv("00", "A000000727") + tlv("01", tlv("00", bank_bin) + tlv("01", account))
    t38 += tlv("02", "QRIBFTTA")
    body = tlv("00", "01") + tlv("01", "12" if amount else "11") + tlv("38", t38)
    body += tlv("53", "704")
    if amount is not None:
        body += tlv("54", str(amount))
    body += tlv("58", "VN")
    body += tlv("62", tlv("08", memo) + extra_62)
    body += "6304"
    return body + f"{crc16_ccitt(body.encode()):04X}"


def render_qr(text: str, size: int = 220, ec_level: str = "M") -> Image.Image:
    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode, ec_level=ec_level)
    arr = np.array(zxingcpp.write_barcode_to_image(barcode, scale=8), dtype=np.uint8)
    img = Image.fromarray(arr, mode="L").convert("RGB")
    return img.resize((size, size), Image.Resampling.NEAREST)


def font(size: int = 22) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def chat_image(left_qr: Image.Image | None, right_qr: Image.Image | None) -> Image.Image:
    """Ảnh chat kiểu Zalo: bong bóng trắng bên trái, bong bóng xanh nhạt bên phải."""
    img = Image.new("RGB", (900, 700), (225, 232, 240))
    d = ImageDraw.Draw(img)
    if left_qr is not None:
        d.rounded_rectangle((30, 40, 360, 380), 18, fill=(255, 255, 255))
        img.paste(left_qr.resize((260, 260)), (50, 70))
    if right_qr is not None:
        d.rounded_rectangle((520, 320, 870, 660), 18, fill=(204, 226, 255))
        img.paste(right_qr.resize((260, 260)), (560, 350))
    return img


def png_with_declared_size(width: int, height: int) -> bytes:
    """PNG chỉ vài trăm byte nhưng header khai báo width×height (bom giải nén)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 16)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
