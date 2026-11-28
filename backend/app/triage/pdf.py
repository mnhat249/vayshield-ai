"""Xuất PDF đơn trình báo (fpdf2). Phông lõi Helvetica không có dấu tiếng Việt nên văn bản được
bỏ dấu (an toàn, vẫn đọc được); nếu tìm thấy phông Unicode trên máy chủ thì dùng phông đó."""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable
from typing import Final

from fpdf import FPDF

from app.core.schemas.triage import GoldenHourDossierResponse

_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
)


def fold_vi(text: str) -> str:
    """Bỏ dấu tiếng Việt (đ -> d)."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _unicode_font() -> str | None:
    return next((p for p in _FONT_CANDIDATES if os.path.isfile(p)), None)


def render_report_pdf(dossier: GoldenHourDossierResponse, *, force_ascii: bool = False) -> bytes:
    font_path = None if force_ascii else _unicode_font()
    pdf = FPDF(format="A4")
    pdf.set_compression(False)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    fix: Callable[[str], str]
    if font_path:
        pdf.add_font("vs", "", font_path)
        pdf.set_font("vs", size=11)
        fix = str
    else:
        pdf.set_font("Helvetica", size=11)
        fix = fold_vi
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def line(text: str) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(width, 6, fix(text))

    line("ĐƠN TRÌNH BÁO - GIỜ VÀNG")
    pdf.ln(2)
    for paragraph in dossier.police_report_draft_vi.split("\n"):
        line(paragraph)
    pdf.ln(3)
    line("Các bước cần làm ngay:")
    for i, step in enumerate(dossier.steps_vi, 1):
        line(f"{i}. {step}")
    pdf.ln(2)
    for h in dossier.bank_freeze_hotlines:
        number = h.hotline or "(chưa có số đã kiểm chứng)"
        line(f"{h.bank_short_name}: {number}")
    line(f"Công an: {dossier.police_hotline}")
    return bytes(pdf.output())
