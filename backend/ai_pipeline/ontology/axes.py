# ruff: noqa: E501  (bảng regex dài, tách dòng làm giảm khả năng đọc)
"""Lớp luật tất định cho 10 trục C1–C10 (File 3 §3.3). Chạy trước mọi LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.schemas.common import OntologyAxis

A = OntologyAxis


def _c(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in patterns)


# Mẫu theo NGỮ NGHĨA trên văn bản đã bỏ dấu (xem text.normalize), không dựa vào 1 từ khóa.
AXIS_PATTERNS: dict[OntologyAxis, tuple[re.Pattern[str], ...]] = {
    A.C1: _c(
        r"\b(huy|tat toan|hoan tra|hoan lai|tra lai|thanh ly)\b.{0,30}\b(khoan vay|khoan tien vay|hop dong vay|khoan nay|mon nay|mon vay)\b",
        r"\btat toan som\b",
        r"\bkhong (muon|con muon|can) vay\b",
        r"\b(hoan|tra)\s+(lai\s+)?.{0,20}\b(vua|moi)\s+(ve|nhan|giai ngan)\b",
        r"\b(giai ngan|khoan vay|vay)\s+nham\b",
        r"\bnham\b.{0,15}\bgiai ngan\b",
    ),
    A.C2: _c(
        r"\b(nhap|ghi|dien|doc)\s+(thieu|sai|nham)\b.{0,25}\b(so|tai khoan)\b",
        r"\b(lenh|tien|giao dich|ho so|tai khoan)\s+(bi\s+)?(treo|dong bang|phong toa|khoa)\b",
        r"\b(giai toa|go bo|mo)\s+(lenh\s+)?(treo|phong toa|khoa|dong bang)\b",
        r"\b(ten|thong tin)\b.{0,25}\b(cccd|can cuoc|cmnd)\b.{0,20}\b(lech|sai|khong khop)\b",
        r"\bkich hoat lai\b",
        r"\bsai\s+(1\s+|mot\s+)?(chu so|so tai khoan)\b",
        r"\bloi\s+(ho so|giai ngan|he thong)\b",
    ),
    A.C3: _c(
        r"\bcic\b.{0,40}\b(chuyen|nop|dong|nang|diem|chung minh)\b",
        r"\b(chuyen|nop|dong|nang)\b.{0,40}\bcic\b",
        r"\b(diem|hang|xep hang)\s+(tin dung|tin nhiem)\b",
        r"\bchung minh\b.{0,20}\b(kha nang|nang luc|tai chinh|dong tien|chi tra|tra no)\b",
        r"\b(dong tien\s+)?doi ung\b",
        r"\btao\s+(lich su\s+(tin dung|vay|giao dich)|dong tien\s+(doi ung|ao))\b",
    ),
    A.C4: _c(
        r"\btai khoan\s+(ky thuat|bao lanh|bao chung|giam sat|tam giu|trung gian|an toan|kho bac|dam bao)\b",
        r"\bket\s+(an toan|dien tu)\b|\bket sat dien tu\b",
        r"\b(chuyen|nop|gui|dong)\b(?:(?!thue|nha nuoc).){0,30}\bkho bac\b(?!\s+nha nuoc)",
        r"\bma\s+(dinh danh\s+)?thu ho\b",
        r"\bvi\s+(trung gian|bao chung|bao lanh|giam sat)\b",
        r"\bgiam sat vien\b",
    ),
    A.C5: _c(
        r"\b(so|lo|hoang)\s+(bi\s+)?(phat|kien|bat|bao)\b",
        r"\b(trong|truoc)\s+\d+\s*(phut|gio|tieng)\b.{0,50}\b(phat|kien|bao|bat|gui giay|khoa|cong an)\b",
        r"\b(phat|kien|bao|bat|gui giay|khoa|cong an)\b.{0,50}\b(trong|truoc)\s+\d+\s*(phut|gio|tieng)\b",
        r"\bgap lam\b.{0,40}\b(bi|phat|kien|bao)\b",
        r"\bkhong\s+(chuyen|nop|tra)\b.{0,40}\b(phat|kien|bao|bat|gui giay|khoa|cong an)\b",
        r"\bgui giay\b.{0,25}\b(cong ty|nha may|noi lam viec)\b",
        r"\bbi\s+(bao|goi|gui)\s+(len|ve|cong an)\b",
        r"\btre\b.{0,25}\bbi\s+(phat|bao|kien)\b",
    ),
    A.C6: _c(
        r"\b(phi|le phi|tien phi)\b.{0,25}\b(giai ngan|rut tien|xu ly khoan|khoan vay|xac nhan giai)\b",
        r"\b(phi|le phi)\s+(lam\s+)?(ho so|xu ly|thu tuc)\b.{0,40}\b(sau khi|truoc khi)\b.{0,15}\b(tien ve|giai ngan|rut|nhan)\b",
        r"\bthue\b.{0,40}\b(rut|giai ngan|khoan vay)\b",
        r"\bbao hiem\b.{0,40}\b(khoan vay|khoan nay|giai ngan|truoc khi rut|de rut|duoc rut)\b",
        r"\b(khoan vay|giai ngan)\b.{0,40}\b(dong|nop)\s+(tien\s+)?(phi\s+)?bao hiem\b",
    ),
    A.C8: _c(
        r"\b(vi tra sau|mua truoc tra sau|bnpl|han muc tra sau)\b",
        r"\bhan muc mua sam\b",
        r"\b(don hang|giao dich|thanh toan)\s+(ao|gia|khong that|khong co that)\b",
        r"\bquet\s+(ma\s+)?(qr\s+)?.{0,30}\b(gia|ao|khong that)\b",
        r"\b(quet|dung)\s+(ma\s+)?qr\b.{0,40}\b(rut|lay|doi ra)\s+tien\b",
        r"\b(rut|lay|doi ra)\s+tien\s+(mat\s+)?(tu|bang)\s+(vi tra sau|han muc|ma qr)\b",
    ),
    A.C9: _c(
        r"\bdung ten\b.{0,25}\b(vay|ho so|hop dong|cccd)\b",
        r"\b(vay|ho so|hop dong)\b.{0,25}\bdung ten\b",
        r"\bvay\s+(ho|gium|dum|giup)\b",
        r"\bhoa hong\b.{0,30}\b(vay|ho so|cccd)\b",
        r"\b(vay|ho so|cccd)\b.{0,30}\bhoa hong\b",
        r"\b(tien cong|tien thuong)\b.{0,20}\b(vay|ho so)\b",
        r"\b(chuyen|gui|dua)\b.{0,12}\b(cho|lai cho)\s+(sep|anh tuyen dung|chi tuyen dung|nguoi tuyen dung|quan ly|cong ty tuyen dung)\b",
        r"\b(muon|cho muon|nho)\b.{0,15}\b(cccd|can cuoc|giay to|ho so)\b.{0,25}\b(vay|di vay)\b",
        r"\b(sep|anh tuyen dung|nguoi tuyen dung)\b.{0,40}\b(lay tien|nhan tien|gui lai|chuyen lai|dua lai)\b",
    ),
    A.C10: _c(
        r"\b(cong an|vien kiem sat|toa an|co quan dieu tra|canh sat|cuc an ninh|can bo|dieu tra vien|kiem sat vien)\b.{0,30}\b(yeu cau|bao|goi|ra lenh|thong bao|lien he)\b.{0,50}\b(chuyen|nop|phong toa|tam giu|dieu tra|rua tien|chung minh)\b",
        r"\brua tien\b",
        r"\bchung minh\b.{0,10}\btrong sach\b",
        r"\b(lenh bat|lenh truy na)\b",
        r"\btai khoan\s+(tam giu|phong toa)\b",
        r"\btam giu\b.{0,30}\bdieu tra\b|\bdieu tra\b.{0,30}\btam giu\b",
    ),
}

# C7: né tránh cực ngắn (File 3 §3.3).
EVASIVE_PHRASES: tuple[re.Pattern[str], ...] = _c(
    r"\bviec rieng\b",
    r"\bchuyen cho (con|nguoi nha|ban|me|bo|em|anh|chi)\b",
    r"\btra (no )?(tien )?hang\b",
    r"\b(tra|chuyen) (no|tien) (cho )?(ban|hang)\b",
    r"\bmua (do|sam|hang)\b",
    r"\b(sinh hoat|ca nhan)\b",
    r"\bkhong co gi\b",
    r"\bchuyen khoan thoi\b",
    r"\bviec (gia dinh|nha)\b",
)
EVASIVE_MAX_WORDS = 4


@dataclass(frozen=True)
class AxisHit:
    axis: OntologyAxis
    confidence: float
    matched: int


def match_axes(normalized: str) -> list[AxisHit]:
    """Trả các trục khớp luật (C7 xử lý riêng vì cần cảm biến)."""
    hits: list[AxisHit] = []
    for axis, patterns in AXIS_PATTERNS.items():
        n = sum(1 for p in patterns if p.search(normalized))
        if n:
            hits.append(AxisHit(axis, min(0.99, 0.88 + 0.04 * (n - 1)), n))
    return hits


def is_evasive(normalized: str) -> bool:
    if not normalized:
        return False
    if any(p.search(normalized) for p in EVASIVE_PHRASES):
        return len(normalized.split()) <= 8
    return len(normalized.split()) <= EVASIVE_MAX_WORDS
