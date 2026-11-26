"""Lớp 1+2: khử chữ tàng hình theo phân cực sáng/tối và khử mộc đỏ HSV (File 3 §2.1)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image

from ai_pipeline.vision.upload_guard import MAX_PIXELS, load_clean_image

U8 = NDArray[np.uint8]
Polarity = Literal["light", "dark", "mid"]

BLOCK = 32
HIDDEN_DELTA = 5  # |ΔY - Ybg| <= 5/255
DARK_MAX = 55
LIGHT_MIN = 200
HIDDEN_SUSPECT_PIXELS = 30


@dataclass(frozen=True)
class SanitizedImage:
    image: Image.Image  # RGB, đã khử chữ tàng hình, không metadata
    polarity: Polarity
    background_luma: float
    hidden_text_pixels: int  # số điểm ảnh bị san phẳng (khác nền nhưng ≤5/255)
    hidden_text_suspected: bool

    def to_png_bytes(self) -> bytes:
        buf = io.BytesIO()
        self.image.save(buf, format="PNG")
        return buf.getvalue()


def _luma(rgb: U8) -> NDArray[np.float32]:
    f = rgb.astype(np.float32)
    return np.asarray(0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2], dtype=np.float32)


def _block_median_color(rgb: U8) -> U8:
    """Màu nền trung vị cục bộ theo khối BLOCK×BLOCK, phóng lại về kích thước ảnh."""
    h, w, _ = rgb.shape
    ph, pw = (-h) % BLOCK, (-w) % BLOCK
    padded = np.pad(rgb, ((0, ph), (0, pw), (0, 0)), mode="edge")
    gh, gw = padded.shape[0] // BLOCK, padded.shape[1] // BLOCK
    blocks = padded.reshape(gh, BLOCK, gw, BLOCK, 3).transpose(0, 2, 1, 3, 4).reshape(gh, gw, -1, 3)
    med = np.median(blocks, axis=2).astype(np.uint8)
    up = np.repeat(np.repeat(med, BLOCK, axis=0), BLOCK, axis=1)
    return np.ascontiguousarray(up[:h, :w])


def classify_polarity(luma_median: float) -> Polarity:
    if luma_median < DARK_MAX:
        return "dark"
    if luma_median > LIGHT_MIN:
        return "light"
    return "mid"


def scrub_hidden_text(rgb: U8) -> tuple[U8, int, float]:
    """San phẳng điểm ảnh gần như trùng nền cục bộ; giữ nguyên chữ tương phản thật.

    Chữ sáng trên nền tối (Dark Mode, EC-AI01) có |ΔY| lớn nên được bảo toàn.
    """
    bg = _block_median_color(rgb)
    delta = np.abs(_luma(rgb) - _luma(bg))
    differs = np.any(rgb != bg, axis=2)
    mask = delta <= HIDDEN_DELTA
    hidden = int(np.count_nonzero(mask & differs))
    out = np.where(mask[..., None], bg, rgb).astype(np.uint8)
    return out, hidden, float(np.median(_luma(rgb)))


def sanitize_image(data: bytes, max_pixels: int = MAX_PIXELS) -> SanitizedImage:
    """Upload → ảnh sạch: kiểm magic/pixel, bỏ metadata, ghép alpha lên nền, khử chữ tàng hình.

    Ném `ImageRejectedError` (EC-DB05/EC-DB06) nếu ảnh không hợp lệ.
    """
    rgba = load_clean_image(data, max_pixels)
    arr = np.asarray(rgba, dtype=np.uint8)
    alpha = arr[..., 3:4].astype(np.float32) / 255.0
    rgb = np.ascontiguousarray(arr[..., :3])
    if np.any(alpha < 1.0):
        # Ghép RGBA lên màu nền trung vị cục bộ (không phải trắng cứng).
        bg_color = _block_median_color(rgb).astype(np.float32)
        rgb = (rgb.astype(np.float32) * alpha + bg_color * (1.0 - alpha)).round().astype(np.uint8)
    cleaned, hidden, med = scrub_hidden_text(np.ascontiguousarray(rgb))
    return SanitizedImage(
        image=Image.fromarray(cleaned, mode="RGB"),
        polarity=classify_polarity(med),
        background_luma=med,
        hidden_text_pixels=hidden,
        hidden_text_suspected=hidden >= HIDDEN_SUSPECT_PIXELS,
    )


# --- Lớp 2: mộc đỏ -----------------------------------------------------------------

RED_SAT_MIN = 0.45
RED_DETECT_RATIO = 0.001
INK_LOCAL_RATIO = 0.7  # tương phản cục bộ so với mực mộc
INK_VALUE_MAX = 0.25  # chỉ điểm đỏ RẤT tối (mực đen lộ qua mộc) mới coi là chữ


@dataclass(frozen=True)
class StampResult:
    image: Image.Image
    detected: bool
    red_ratio: float
    overlaps_text: bool


def strip_red_stamp(image: Image.Image) -> StampResult:
    """HSV Red-Stamp Suppression (EC-AI05): H∈[0,12°]∪[345°,360°], S>0.45.

    Điểm đỏ sáng → màu nền cục bộ; điểm đỏ tối (chữ số đen nằm dưới mộc) → mực đen.
    """
    rgb = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]  # OpenCV: H 0..179 (độ/2)
    red = ((hue <= 6) | (hue >= 173)) & (sat > RED_SAT_MIN * 255)
    ratio = float(np.count_nonzero(red)) / red.size
    if ratio < RED_DETECT_RATIO:
        return StampResult(Image.fromarray(rgb, mode="RGB"), False, ratio, False)

    # Mực = V rất thấp, hoặc tối rõ rệt so với nền mộc cục bộ (trung vị V vùng đỏ).
    stamp_v = float(np.median(val[red]))
    ink = red & ((val < INK_VALUE_MAX * 255) | (val < INK_LOCAL_RATIO * stamp_v))
    dark_text = (val < 110) & (sat < 90)
    near_red = cv2.dilate(red.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    overlaps = bool(np.any(ink) or np.any(near_red & dark_text))

    bg = _block_median_color(np.where(red[..., None], np.uint8(255), rgb).astype(np.uint8))
    out = rgb.copy()
    out[red] = bg[red]
    out[ink] = 0
    return StampResult(Image.fromarray(out, mode="RGB"), True, ratio, overlaps)
