"""Lớp 4: giám định biên lai giả — ELA + bất biến nghiệp vụ NAPAS 247 (EC-AI06)."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageChops

from ai_pipeline.vision.text import fold_diacritics

ELA_QUALITY = 90
ELA_RATIO_THRESHOLD = 3.0
ELA_MIN_REGION_MEAN = 2.0

_FAST = re.compile(r"\b(chuyen (khoan )?nhanh|napas ?247|24/7|247|fast ?transfer|ck nhanh)\b")
_STUCK = re.compile(
    r"\b(treo|bi treo|dang cho|cho (nguoi nhan|xu ly|giai toa|duyet)|"
    r"pending|bi giu|tam giu|dong bang|phong toa)\b"
)
_PAY = r"(?:nop|(?<!\d)(?<!\d )dong|chuyen them|gui them|thanh toan)"
_UNLOCK = r"(?:giai toa|mo bang|mo khoa|giai ngan|kich hoat)"
_FEE = re.compile(
    rf"\b{_PAY}\b.{{0,40}}\b{_UNLOCK}\b"
    rf"|\b{_UNLOCK}\b.{{0,40}}\b{_PAY}\b"
    rf"|\b(?:nop|chuyen them)\b.{{0,12}}\d+ ?%"
)


@dataclass(frozen=True)
class ReceiptForensics:
    fake_receipt_napas_impossible: bool  # cờ FAKE_RECEIPT_NAPAS_IMPOSSIBLE
    ela_suspicious: bool
    ela_ratio: float | None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_suspected_fake(self) -> bool:
        return self.fake_receipt_napas_impossible or self.ela_suspicious


def napas_247_impossible(ocr_text: str) -> bool:
    """Biên lai 'CK nhanh 24/7' nhưng treo + đòi người nhận nộp phí → bất khả thi."""
    t = fold_diacritics(ocr_text)
    return bool(_FAST.search(t) and _STUCK.search(t) and _FEE.search(t))


def ela_map(image: Image.Image, quality: int = ELA_QUALITY) -> NDArray[np.float32]:
    """Error Level Analysis: |ảnh - ảnh nén lại JPEG q|, theo kênh tối đa (float32 HxW)."""
    rgb = image.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    with Image.open(buf) as re_img:
        diff = ImageChops.difference(rgb, re_img.convert("RGB"))
    return np.asarray(diff, dtype=np.float32).max(axis=2)


def ela_region_ratio(
    image: Image.Image, region: tuple[int, int, int, int]
) -> tuple[float, float, float]:
    """(ELA trung bình vùng, ELA trung bình phần còn lại, tỉ lệ vùng/còn lại)."""
    ela = ela_map(image)
    h, w = ela.shape
    x0, y0 = max(0, min(w, region[0])), max(0, min(h, region[1]))
    x1, y1 = max(0, min(w, region[2])), max(0, min(h, region[3]))
    mask = np.zeros(ela.shape, bool)
    mask[y0:y1, x0:x1] = True
    if not mask.any() or mask.all():
        raise ValueError("region không hợp lệ")
    inside, outside = float(ela[mask].mean()), float(ela[~mask].mean())
    return inside, outside, inside / max(outside, 0.05)


def receipt_forensics(
    ocr_text: str | None = None,
    image: Image.Image | None = None,
    amount_region: tuple[int, int, int, int] | None = None,
) -> ReceiptForensics:
    """Kết hợp ELA quanh vùng số tiền/STK và bất biến NAPAS 247 (FAKE_RECEIPT_NAPAS_IMPOSSIBLE)."""
    reasons: list[str] = []
    napas = bool(ocr_text) and napas_247_impossible(ocr_text or "")
    if napas:
        reasons.append("FAKE_RECEIPT_NAPAS_IMPOSSIBLE")
    ratio: float | None = None
    ela_flag = False
    if image is not None and amount_region is not None:
        inside, _, ratio = ela_region_ratio(image, amount_region)
        ela_flag = ratio >= ELA_RATIO_THRESHOLD and inside >= ELA_MIN_REGION_MEAN
        if ela_flag:
            reasons.append("ELA_ANOMALY_NEAR_AMOUNT")
    return ReceiptForensics(napas, ela_flag, ratio, tuple(reasons))
