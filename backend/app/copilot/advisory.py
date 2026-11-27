"""Copilot B2B (File 3 §5.2): Advisory Writer + Strict Outbound Sanitizer (EC-AI21).

Mọi bản nháp cảnh báo (kể cả do LLM sinh) bị tước 100% URL, tên miền, email, SĐT/hotline lạ và mọi
biến mẫu ``{{...}}`` ngoài ``{{OFFICIAL_HOTLINE}}``. Số hotline chính thức KHÔNG bao giờ được viết
trong văn bản: chỉ chèn biến mẫu để nhân sự điền từ bảng cấu hình cứng của tổ chức. Dữ liệu từ ảnh
lừa đảo (do kẻ tấn công kiểm soát) không bao giờ được đưa vào bản nháp: mẫu chỉ dùng trường có cấu
trúc (kiểu bẫy, mức độ, tên bên cho vay trong danh bạ).

Hàm sanitize là thuần, không log nội dung.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Protocol

from app.core.schemas.ops import ScamTrap, SeverityLevel

OFFICIAL_HOTLINE_VAR: Final = "{{OFFICIAL_HOTLINE}}"
_SENTINEL: Final = "\u0001OFFICIAL\u0001"
MIN_USEFUL_CHARS: Final = 40

_ZERO_WIDTH_RE: Final = re.compile(r"[​-‏⁠﻿­]")
_TEMPLATE_RE: Final = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_OBF_DOT_RE: Final = re.compile(
    r"\s*(?:\[\s*\.\s*\]|\(\s*\.\s*\)|\{\s*\.\s*\}|\s+dot\s+|\s+chấm\s+)\s*"
)
_OBF_SCHEME_RE: Final = re.compile(r"\bh(?:xx|tt)p(s?)\s*(?:://|:\\\\|\[:\]//)", re.IGNORECASE)
_URL_RE: Final = re.compile(
    r"(?:\b[a-z][a-z0-9+.\-]{1,15}://\S+)"  # có scheme
    r"|(?:\bwww\.\S+)"
    r"|(?:\b(?:[a-z0-9\-]+\.)+[a-z]{2,24}\b(?:[/:?#]\S*)?)",  # tên miền trần (zalo.me/x, bit.ly/y)
    re.IGNORECASE,
)
_EMAIL_RE: Final = re.compile(r"[^\s@]+@[^\s@]+", re.IGNORECASE)
# SĐT: 0/+84 + 8-10 số; hotline 18xx/19xx; hoặc >= 9 chữ số liền/cách bằng space/gạch.
_PHONE_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?<![\w.])(?:\+\s*84|84|0)(?:[\s.\-()]*\d){8,10}(?!\d)"),
    re.compile(r"(?<!\d)1[89]00(?:[\s.\-]*\d){4,8}(?!\d)"),
    re.compile(r"(?<![\w.])\d(?:[ \-]?\d){8,}(?!\d)"),
)
_DIGIT_WORD: Final = (
    r"(?:không|một|hai|ba|bốn|tư|năm|lăm|sáu|bảy|tám|chín"
    r"|zero|one|two|three|four|five|six|seven|eight|nine)"
)
_SPOKEN_PHONE_RE: Final = re.compile(
    rf"(?<!\w)(?:{_DIGIT_WORD}[\s,.\-]+){{6,}}{_DIGIT_WORD}(?!\w)", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    text: str
    removed: tuple[str, ...]  # loại đã tước: url|email|phone|template


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def sanitize_outbound(text: str, *, whitelist_hotlines: Iterable[str] = ()) -> SanitizeResult:
    """Strict Outbound Sanitizer. ``whitelist_hotlines``: số chính thức (cấu hình cứng) — nếu bản
    nháp chứa đúng số đó thì thay bằng ``{{OFFICIAL_HOTLINE}}`` (không để số rõ trong văn bản)."""
    allowed = {d for h in whitelist_hotlines if (d := _digits(h))}
    removed: list[str] = []
    out = unicodedata.normalize("NFKC", text)
    out = _ZERO_WIDTH_RE.sub("", out)
    out = out.replace(OFFICIAL_HOTLINE_VAR, _SENTINEL)

    def strip_templates(m: re.Match[str]) -> str:
        removed.append("template")
        return ""

    out = _TEMPLATE_RE.sub(strip_templates, out)
    out = _OBF_SCHEME_RE.sub(lambda m: f"http{m.group(1)}://", out)
    out = _OBF_DOT_RE.sub(".", out)

    def strip_email(m: re.Match[str]) -> str:
        removed.append("email")
        return " "

    out = _EMAIL_RE.sub(strip_email, out)

    def strip_url(m: re.Match[str]) -> str:
        removed.append("url")
        return " "

    out = _URL_RE.sub(strip_url, out)

    def strip_phone(m: re.Match[str]) -> str:
        digits = _digits(m.group(0))
        if digits in allowed or ("84" + digits[1:] in allowed if digits.startswith("0") else False):
            return _SENTINEL
        removed.append("phone")
        return " "

    for pattern in _PHONE_RES:
        out = pattern.sub(strip_phone, out)

    def strip_spoken(m: re.Match[str]) -> str:
        removed.append("phone")
        return " "

    out = _SPOKEN_PHONE_RE.sub(strip_spoken, out)
    out = re.sub(r"[ \t]{2,}", " ", out).replace(" ,", ",").replace(" .", ".").strip()
    out = out.replace(_SENTINEL, OFFICIAL_HOTLINE_VAR)
    return SanitizeResult(text=out, removed=tuple(dict.fromkeys(removed)))


def is_outbound_safe(text: str) -> bool:
    """True nếu ``text`` không còn URL/email/SĐT/biến mẫu lạ (dùng để kiểm lại đầu ra)."""
    result = sanitize_outbound(text)
    return result.text == text.strip() and not result.removed


@dataclass(frozen=True, slots=True)
class AdvisoryContext:
    """Chỉ gồm trường có cấu trúc do máy chủ tính — KHÔNG có văn bản tự do từ ảnh/người dùng."""

    trap: ScamTrap
    severity: SeverityLevel
    lender_name: str | None = None
    incident_count_24h: int = 0


@dataclass(frozen=True, slots=True)
class AdvisoryDraft:
    text: str
    used_llm: bool
    sanitized_removed: tuple[str, ...]
    requires_human_approval: bool = True  # Human-in-the-Loop


class AdvisoryLLM(Protocol):
    """Adapter LLM tùy chọn (đã mock được): trả bản nháp tiếng Việt từ ngữ cảnh có cấu trúc."""

    async def draft(self, context: AdvisoryContext) -> str: ...


_TRAP_BODY: Final[dict[ScamTrap, str]] = {
    ScamTrap.TRAP_1_ADVANCE_FEE: (
        "đang xuất hiện chiêu {actor} yêu cầu nộp phí trước (phí bảo hiểm, phí mở băng, phí giải "
        "ngân) để được nhận khoản vay. Đơn vị cho vay hợp pháp không đòi chuyển tiền cho cá nhân "
        "trước khi giải ngân."
    ),
    ScamTrap.TRAP_2_SHARK_APK: (
        "đang xuất hiện ứng dụng vay tiền {actor} yêu cầu cài tệp APK ngoài cửa hàng ứng dụng "
        "chính thức và xin quyền đọc danh bạ, tin nhắn. Không cài đặt ứng dụng từ nguồn lạ."
    ),
    ScamTrap.TRAP_3_LOAN_RECALL: (
        "đang xuất hiện cuộc gọi {actor} tự xưng nhân viên nhắc nợ/thu hồi khoản vay và yêu cầu "
        "chuyển tiền vào tài khoản cá nhân. Hãy kiểm tra lại khoản vay trong ứng dụng chính thức."
    ),
}


def _template_draft(ctx: AdvisoryContext) -> str:
    actor = f"mạo danh {ctx.lender_name}" if ctx.lender_name else "mạo danh tổ chức cho vay"
    urgency = (
        "KHẨN: "
        if ctx.severity is SeverityLevel.CRITICAL_WAVE
        else ("Lưu ý: " if ctx.severity is SeverityLevel.ELEVATED else "")
    )
    body = _TRAP_BODY[ctx.trap].format(actor=actor)
    return (
        f"{urgency}Cảnh báo lừa đảo vay tiền: {body} Tuyệt đối không cung cấp mã OTP và không "
        f"chuyển tiền vào tài khoản lạ. Cần hỗ trợ, vui lòng liên hệ tổng đài chính thức: "
        f"{OFFICIAL_HOTLINE_VAR}."
    )


class AdvisoryWriter:
    """Soạn nháp cảnh báo khách hàng (In-App/ZNS). Luôn qua sanitizer; LLM lỗi/rác -> mẫu cứng."""

    def __init__(
        self,
        llm: AdvisoryLLM | None = None,
        *,
        whitelist_hotlines: Iterable[str] = (),
    ) -> None:
        self._llm = llm
        self._whitelist = tuple(whitelist_hotlines)

    async def draft(self, ctx: AdvisoryContext) -> AdvisoryDraft:
        removed: tuple[str, ...] = ()
        if self._llm is not None:
            try:
                raw = await self._llm.draft(ctx)
            except Exception:
                raw = ""
            clean = sanitize_outbound(raw, whitelist_hotlines=self._whitelist)
            if len(clean.text) >= MIN_USEFUL_CHARS:
                return AdvisoryDraft(clean.text, used_llm=True, sanitized_removed=clean.removed)
            removed = clean.removed
        # Mẫu cứng cũng đi qua sanitizer (tên bên cho vay đến từ danh bạ nhưng vẫn kiểm).
        final = sanitize_outbound(_template_draft(ctx), whitelist_hotlines=self._whitelist)
        return AdvisoryDraft(
            final.text,
            used_llm=False,
            sanitized_removed=tuple(dict.fromkeys(removed + final.removed)),
        )


@dataclass(frozen=True, slots=True)
class ShieldTriggerSuggestion:
    """Tác vụ 2 — gợi ý (không tự thực thi) kích hoạt Khiên Cảnh Giác Cao 24h cho nhóm khách hàng
    có hồ sơ tương đồng; luôn cần người duyệt (Human-in-the-Loop)."""

    cluster_id_hint: str
    suggested_shield_hours: int
    rationale_vi: str
    requires_human_approval: bool = True


def suggest_shield_trigger(
    severity: SeverityLevel, incident_count_24h: int
) -> ShieldTriggerSuggestion | None:
    if severity is SeverityLevel.WATCH:
        return None
    return ShieldTriggerSuggestion(
        cluster_id_hint="same-cohort",
        suggested_shield_hours=24,
        rationale_vi=(
            f"Chiến dịch ở mức {severity.value} với {incident_count_24h} sự cố trong 24 giờ: nên "
            "bật Khiên Cảnh Giác Cao 24h cho nhóm khách hàng vừa giải ngân có hồ sơ tương đồng."
        ),
    )
