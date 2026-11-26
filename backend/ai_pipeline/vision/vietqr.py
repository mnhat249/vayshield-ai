"""Lớp 3: giải mã EMVCo/NAPAS VietQR (CRC16, Tag 38/54/62) + phân định bong bóng chat trái/phải.

Dữ liệu trong QR là KHÔNG TIN CẬY (EC-AI04): luôn nằm trong `UntrustedQrPayload`, chỉ đưa vào
kiểm tra BIN/STK tất định, không bao giờ nối vào System Prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

import cv2
import numpy as np
import zxingcpp
from numpy.typing import NDArray
from PIL import Image

from ai_pipeline.vision.text import fold_diacritics

U8 = NDArray[np.uint8]
Box = tuple[int, int, int, int]


class VietQrError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) theo EMVCo."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def parse_tlv(s: str) -> dict[str, str]:
    """Parse chuỗi TLV (tag 2 ký tự, độ dài 2 chữ số, giá trị). Ném VietQrError nếu hỏng."""
    out: dict[str, str] = {}
    i = 0
    while i < len(s):
        if i + 4 > len(s):
            raise VietQrError("TLV_TRUNCATED", "Chuỗi TLV bị cắt cụt.")
        tag, raw_len = s[i : i + 2], s[i + 2 : i + 4]
        if not (tag.isdigit() and raw_len.isdigit()):
            raise VietQrError("TLV_MALFORMED", "Tag/độ dài TLV không hợp lệ.")
        end = i + 4 + int(raw_len)
        if end > len(s):
            raise VietQrError("TLV_TRUNCATED", "Độ dài TLV vượt quá dữ liệu.")
        out.setdefault(tag, s[i + 4 : end])
        i = end
    return out


_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"ignore\b.{0,30}\b(rule|instruction|prompt|previous|above)",
        r"disregard\b.{0,30}\b(rule|instruction|prompt|previous|above)",
        r"\b(return|output|answer|respond|set|mark)\b.{0,20}\b(safe|green|an toan|hop phap)\b",
        r"system\s*prompt|developer\s*message|you are (now )?(a|an|the)\b|act as\b",
        r"\b(bo qua|quen|huy)\b.{0,30}\b(quy tac|huong dan|luat|lenh)\b",
        r"\b(tra ve|ket luan|danh dau)\b.{0,20}\b(an toan|hop phap|xanh)\b",
        r"</?\s*(system|assistant|user|instruction)\s*>|\[\s*inst\s*\]|```",
        r"\bverdict\b|\bv_green\b",
    )
)


def scan_injection(text: str) -> list[str]:
    """Trả về các mẫu prompt-injection khớp (đã khử dấu/homoglyph); rỗng nếu sạch."""
    folded = fold_diacritics(text)
    return [p.pattern for p in _INJECTION_PATTERNS if p.search(folded)]


@dataclass(frozen=True)
class UntrustedQrPayload:
    """Thẻ định kiểu: nội dung do kẻ gian kiểm soát. KHÔNG đưa vào prompt LLM."""

    memo: str
    bill_number: str | None = None
    injection_detected: bool = False
    injection_matches: tuple[str, ...] = ()


@dataclass(frozen=True)
class VietQrPayload:
    bank_bin: str
    account_number: str
    service_code: str | None
    amount_vnd: int | None  # Tag 54, int đồng; None nếu QR không cài số tiền
    currency: str | None
    dynamic: bool  # Tag 01 == "12"
    crc_valid: bool
    untrusted: UntrustedQrPayload
    raw_tags: dict[str, str] = field(default_factory=dict, repr=False)


def decode_vietqr(payload: str) -> VietQrPayload:
    """Giải mã chuỗi VietQR. Ném `VietQrError` nếu CRC sai / thiếu Tag 38 / TLV hỏng."""
    s = payload.strip()
    if len(s) < 8 or s[-8:-4] != "6304":
        raise VietQrError("CRC_MISSING", "Thiếu Tag 63 (CRC16) ở cuối chuỗi.")
    expected = f"{crc16_ccitt(s[:-4].encode('utf-8')):04X}"
    if s[-4:].upper() != expected:
        raise VietQrError("CRC_MISMATCH", "CRC16 không khớp: mã QR bị sửa hoặc hỏng.")

    tags = parse_tlv(s)
    if tags.get("00") != "01":
        raise VietQrError("NOT_VIETQR", "Tag 00 (Payload Format Indicator) phải là 01.")
    if "53" in tags and tags["53"] != "704":
        raise VietQrError("BAD_CURRENCY", "Tag 53 phải là 704 (VND).")
    if "38" not in tags:
        raise VietQrError("NOT_VIETQR", "Không có Tag 38 (NAPAS VietQR).")
    t38 = parse_tlv(tags["38"])
    if not t38.get("00", "").upper().startswith("A000000727"):
        raise VietQrError("NOT_VIETQR", "GUID Tag 38 không phải NAPAS (A000000727).")
    acct = parse_tlv(t38.get("01", ""))
    bank_bin, account = acct.get("00", ""), acct.get("01", "")
    if not (bank_bin.isdigit() and len(bank_bin) == 6):
        raise VietQrError("BAD_BIN", "Mã BIN ngân hàng phải gồm 6 chữ số.")
    if not (account.isalnum() and 1 <= len(account) <= 19):
        raise VietQrError("BAD_ACCOUNT", "Số tài khoản/thẻ không hợp lệ.")

    amount: int | None = None
    if "54" in tags:
        if not re.fullmatch(r"[0-9]{1,13}(\.[0-9]{1,2})?", tags["54"]):
            raise VietQrError("BAD_AMOUNT", "Tag 54 phải khớp dạng số tiền hợp lệ.")
        try:
            dec = Decimal(tags["54"])
        except InvalidOperation as exc:
            raise VietQrError("BAD_AMOUNT", "Tag 54 không phải số.") from exc
        if dec != dec.to_integral_value():
            raise VietQrError("BAD_AMOUNT", "Số tiền VND phải là số nguyên không âm.")
        amount = int(dec)

    memo, bill = "", None
    scan_text = ""
    if "62" in tags:
        t62 = parse_tlv(tags["62"])
        memo, bill = t62.get("08", ""), t62.get("01")
        scan_text = " ".join(t62.values())  # quét MỌI sub-tag, không chỉ nội dung
    matches = scan_injection(scan_text) if scan_text else []
    return VietQrPayload(
        bank_bin=bank_bin,
        account_number=account,
        service_code=t38.get("02"),
        amount_vnd=amount,
        currency=tags.get("53"),
        dynamic=tags.get("01") == "12",
        crc_valid=True,
        untrusted=UntrustedQrPayload(memo, bill, bool(matches), tuple(matches)),
        raw_tags=tags,
    )


# --- Quét ảnh + phân định người gửi ------------------------------------------------


class QrSender(StrEnum):
    SELF = "SELF"  # QR của chính người dùng (bong bóng phải/xanh) → loại khỏi kiểm tra mồi
    COUNTERPARTY = "COUNTERPARTY"  # QR của đối phương → đưa vào kiểm tra
    UNKNOWN = "UNKNOWN"  # không phân định được → an toàn: vẫn kiểm tra


@dataclass(frozen=True)
class QrDetection:
    text: str
    x_center: float
    y_center: float
    bbox: Box  # x0, y0, x1, y1


@dataclass(frozen=True)
class AttributedQr:
    detection: QrDetection
    sender: QrSender
    reason: str

    @property
    def should_check(self) -> bool:
        return self.sender is not QrSender.SELF


def _read(arr: U8) -> list[QrDetection]:
    found: list[QrDetection] = []
    results = zxingcpp.read_barcodes(arr, formats=zxingcpp.BarcodeFormat.QRCode)  # type: ignore[arg-type]
    for r in results:
        if not r.text:
            continue
        pos = r.position
        pts = [pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left]
        xs, ys = [p.x for p in pts], [p.y for p in pts]
        box = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        found.append(QrDetection(r.text, sum(xs) / 4, sum(ys) / 4, box))
    return found


def _dewarp_candidates(gray: U8) -> list[tuple[U8, Box]]:
    """4-point homography: tìm tứ giác lớn nhất, nắn phẳng (EC-AI03). Trả (ảnh nắn, bbox gốc)."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    th = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 8
    )
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[tuple[U8, Box]] = []
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        if cv2.contourArea(c) < 0.01 * gray.size:
            continue
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) != 4:
            continue
        pts = approx.reshape(4, 2).astype(np.float32)
        s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
        src = np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]])
        side = 480
        dst = np.array([[0, 0], [side, 0], [side, side], [0, side]], np.float32)
        warped = cv2.warpPerspective(gray, cv2.getPerspectiveTransform(src, dst), (side, side))
        padded = cv2.copyMakeBorder(warped, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)
        box = (
            int(pts[:, 0].min()),
            int(pts[:, 1].min()),
            int(pts[:, 0].max()),
            int(pts[:, 1].max()),
        )
        out.append((np.ascontiguousarray(padded), box))
    return out


UPSCALE_MAX_PIXELS = 4_000_000


def _iou(a: Box, b: Box) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _local_contrast(gray: U8) -> U8:
    """Trừ nền cục bộ rồi khuếch đại: cứu QR mờ/tương phản thấp.

    Nền ước lượng trên bản thu nhỏ (1/4) để tiết kiệm CPU/RAM; phép trừ dùng int16.
    """
    h, w = gray.shape
    small = cv2.resize(gray, (max(1, w // 4), max(1, h // 4)), interpolation=cv2.INTER_AREA)
    bg = cv2.GaussianBlur(small.astype(np.float32), (0, 0), 6.0)
    bg_full = cv2.resize(bg, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.int16)
    flat = (gray.astype(np.int16) - bg_full) * 8 + 128
    return np.asarray(np.clip(flat, 0, 255), dtype=np.uint8)


def _merge(found: list[QrDetection], new: list[QrDetection]) -> None:
    for d in new:
        if not any(d.text == f.text and _iou(d.bbox, f.bbox) > 0.3 for f in found):
            found.append(d)


def detect_qr_codes(image: Image.Image) -> list[QrDetection]:
    """Quét mọi QR trong ảnh.

    LUÔN chạy mọi biến thể (gốc, tương phản cục bộ, CLAHE, ngưỡng thích nghi, x2, dewarp 4
    điểm; x2 chỉ khi ảnh < 4MP) và gộp kết quả (dedupe theo text + IoU bbox): QR mờ của kẻ
    gian không được bị bỏ chỉ vì đã đọc được QR rõ khác trong cùng ảnh (EC-AI02).
    """
    rgb = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
    found = _read(rgb)
    gray = np.asarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
    clahe = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8)).apply(gray)
    adaptive = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 10
    )
    variants: list[tuple[NDArray[np.uint8], float]] = [
        (_local_contrast(gray), 1.0),
        (np.asarray(clahe, dtype=np.uint8), 1.0),
        (np.asarray(adaptive, dtype=np.uint8), 1.0),
    ]
    if gray.size < UPSCALE_MAX_PIXELS:  # phóng ×2 chỉ với ảnh nhỏ (tránh bùng RAM/CPU)
        variants.append(
            (
                np.asarray(
                    cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC),
                    dtype=np.uint8,
                ),
                2.0,
            )
        )
    for variant, scale in variants:
        _merge(
            found,
            [
                QrDetection(
                    d.text,
                    d.x_center / scale,
                    d.y_center / scale,
                    (
                        int(d.bbox[0] / scale),
                        int(d.bbox[1] / scale),
                        int(d.bbox[2] / scale),
                        int(d.bbox[3] / scale),
                    ),
                )
                for d in _read(np.ascontiguousarray(variant))
            ],
        )
    known = {d.text for d in found}
    for warped, box in _dewarp_candidates(gray):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        # Tọa độ ảnh nắn phẳng vô nghĩa: chỉ nhận text CHƯA thấy ở bước trước.
        extra = [QrDetection(d.text, cx, cy, box) for d in _read(warped) if d.text not in known]
        known.update(d.text for d in extra)
        found.extend(extra)
    return found


def _bubble_color(rgb: U8, bbox: Box) -> tuple[float, float, float]:
    """Màu trung vị của vành đai ngoài quiet-zone quanh QR (màu bong bóng chat)."""
    x0, y0, x1, y1 = bbox
    pad = max(8, int(0.35 * max(x1 - x0, y1 - y0)))
    inner = pad // 2
    h, w, _ = rgb.shape
    mask = np.zeros((h, w), bool)
    mask[max(0, y0 - pad) : min(h, y1 + pad), max(0, x0 - pad) : min(w, x1 + pad)] = True
    mask[max(0, y0 - inner) : min(h, y1 + inner), max(0, x0 - inner) : min(w, x1 + inner)] = False
    px = rgb[mask]
    if px.size == 0:
        return (255.0, 255.0, 255.0)
    med = np.median(px, axis=0)
    return float(med[0]), float(med[1]), float(med[2])


def _is_blue_bubble(color: tuple[float, float, float]) -> bool:
    r, g, b = color
    return b - r >= 25 and b >= g


def _bubble_edge(rgb: U8, bbox: Box, color: tuple[float, float, float], step: int) -> int | None:
    """Mép bong bóng theo hướng `step` (+1 phải, -1 trái): bỏ qua quiet-zone của QR rồi quét
    khi màu còn khớp màu bong bóng. Trả None nếu không thấy màu bong bóng quanh QR."""
    x0, y0, x1, y1 = bbox
    h, w, _ = rgb.shape
    pad = max(8, int(0.35 * max(x1 - x0, y1 - y0)))
    row = rgb[min(h - 1, max(0, (y0 + y1) // 2))].astype(np.float32)
    ref = np.array(color, np.float32)

    def match(i: int) -> bool:
        return bool(np.abs(row[i] - ref).max() <= 18)

    x = min(w - 1, x1) if step > 0 else max(0, x0)
    limit = min(w - 1, x1 + pad) if step > 0 else max(0, x0 - pad)
    while x != limit and not match(x):
        x += step
    if not match(x):
        return None
    while 0 <= x + step < w and match(x + step):
        x += step
    return x + 1 if step > 0 else x


def attribute_qr_sender(
    image: Image.Image,
    detections: list[QrDetection] | None = None,
    *,
    right_edge_min: float = 0.9,
) -> list[AttributedQr]:
    """Left-vs-Right Chat Bubble Attribution (EC-AI02).

    Chỉ kết luận SELF (loại khỏi kiểm tra mồi) khi có bong bóng xanh thật: mép trái rõ (>0.1W),
    mép phải trong ảnh và căn phải (>=0.9W), tâm QR > 0.5W (nền xanh phủ kín ảnh KHÔNG phải
    bong bóng). Bong bóng trắng/xám -> COUNTERPARTY; còn lại -> COUNTERPARTY nếu tâm QR ở nửa
    trái (< 0.48W), ngược lại UNKNOWN. Mọi kết quả khác SELF đều `should_check`.
    """
    dets = detect_qr_codes(image) if detections is None else detections
    rgb = np.ascontiguousarray(np.asarray(image.convert("RGB"), dtype=np.uint8))
    width = rgb.shape[1]
    result: list[AttributedQr] = []
    for d in dets:
        color = _bubble_color(rgb, d.bbox)
        if _is_blue_bubble(color):
            right = _bubble_edge(rgb, d.bbox, color, +1)
            left = _bubble_edge(rgb, d.bbox, color, -1)
            # SELF chỉ khi có bong bóng thật: mép trái rõ (>0.1W), mép phải trong ảnh
            # (<W-2, không phải nền phủ kín), căn phải (>=0.9W) và QR ở nửa phải (>0.5W).
            if (
                right is not None
                and left is not None
                and left > 0.1 * width
                and right < width - 2
                and right >= right_edge_min * width
                and d.x_center > 0.5 * width
            ):
                result.append(AttributedQr(d, QrSender.SELF, "right_aligned_blue_bubble"))
            else:
                result.append(AttributedQr(d, QrSender.UNKNOWN, "blue_bubble_not_right_aligned"))
        elif min(color) >= 200:
            result.append(AttributedQr(d, QrSender.COUNTERPARTY, "white_bubble"))
        elif d.x_center / width < 0.48:
            result.append(AttributedQr(d, QrSender.COUNTERPARTY, "x_center<0.48W"))
        else:
            result.append(AttributedQr(d, QrSender.UNKNOWN, "ambiguous_bubble"))
    return result
