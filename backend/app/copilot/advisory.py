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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Final, Protocol

from app.core.schemas.ops import ScamTrap, SeverityLevel

OFFICIAL_HOTLINE_VAR: Final = "{{OFFICIAL_HOTLINE}}"
_SENTINEL: Final = "\u0001OFFICIAL\u0001"
MIN_USEFUL_CHARS: Final = 40

_ZERO_WIDTH_RE: Final = re.compile("[​-‏‪-‮⁠-⁤᠎͏﻿­]")
_TEMPLATE_RE: Final = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# Dấu chấm Unicode và chữ giả dạng Latin (Cyrillic/Hy Lạp) -> ASCII TRƯỚC khi dò tên miền/SĐT.
_DOT_TABLE: Final = str.maketrans({"。": ".", "｡": ".", "․": ".", "‧": ".", "·": "."})
_HOMOGLYPHS: Final = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "ӏ": "l", "һ": "h", "ԛ": "q", "ԝ": "w", "к": "k", "м": "m", "н": "h",
    "т": "t", "в": "b", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ο": "o", "ρ": "p", "τ": "t",
    "υ": "u", "χ": "x", "ν": "v",
}  # fmt: skip
_HOMOGLYPH_TABLE: Final = str.maketrans(
    {**_HOMOGLYPHS, **{k.upper(): v.upper() for k, v in _HOMOGLYPHS.items()}}
)

_OBF_DOT_RE: Final = re.compile(
    r"\s*[\[({<]\s*(?:dot|chấm|\.)\s*[\])}>]\s*"  # (dot) [dot] {.} (.)
    r"|\s+(?:dot|chấm)\s+"  # "evil dot com"
    r"|(?<=\w)\s+\.\s*(?=\w)",  # "zalo . me" (khoảng trắng TRƯỚC dấu chấm)
    re.IGNORECASE,
)
_OBF_AT_RE: Final = re.compile(
    r"\s*[\[({<]\s*(?:at|a còng|a cờ ng)\s*[\])}>]\s*|(?<=\w)\s+@\s*(?=\w)", re.I
)
_OBF_SCHEME_RE: Final = re.compile(r"(?<!\w)h(?:xx|tt)p(s?)\s*(?:://|:\\|\[:\]//)", re.IGNORECASE)
_IP_RE: Final = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?(?:[/?#]\S*)?(?![\w])")
_URL_RE: Final = re.compile(
    r"(?:(?<!\w)[a-z][a-z0-9+.\-]{1,15}://\S+)"  # có scheme
    r"|(?:(?<!\w)www\.\S+)"
    # tên miền trần (zalo.me/x, bit.ly/y, vay-nhanh.việt): nhãn + TLD chữ cái Unicode
    r"|(?:(?<![\w.])(?:[\w\-]+\.)+[^\W\d_]{2,24}(?![\w])(?:[/:?#]\S*)?)",
    re.IGNORECASE,
)
_EMAIL_RE: Final = re.compile(r"[^\s@]+@[^\s@]+", re.IGNORECASE)
_HANDLE_RE: Final = re.compile(r"@\w[\w.]{1,}")  # @scam_support_vn (Telegram/Zalo/IG...)
_ENCODED_RE: Final = re.compile(r"[A-Za-z0-9+/_\-]{20,}={0,2}")  # base64/hex dài (URL mã hóa)
# Dấu ngăn thường gặp khi kẻ gian tách chữ số để lách bộ lọc.
_SEP: Final = r"[\s.\-_/\\()·,]"
_PHONE_RES: Final[tuple[re.Pattern[str], ...]] = (
    # 0/84/+84 + 8-10 chữ số, ngăn cách bằng dấu bất kỳ ở trên (gộp chữ số trước khi bắt).
    re.compile(rf"(?<![\w.])(?:\+\s*84|84|0)(?:{_SEP}{{0,3}}\d){{8,10}}(?!\d)"),
    re.compile(
        rf"(?<!\d)1{_SEP}{{0,3}}[89]{_SEP}{{0,3}}0{_SEP}{{0,3}}0(?:{_SEP}{{0,3}}\d){{4,8}}(?!\d)"
    ),
    # >= 9 chữ số tách bằng space/gạch/gạch chéo (KHÔNG dấu chấm: số tiền 1.200.000).
    re.compile(r"(?<![\w.])\d(?:[\s\-_/]{0,3}\d){8,}(?!\d)"),
    re.compile(r"(?<![\w.])\d{7,}(?!\d)"),  # chốt chặn: không để dãy >= 7 chữ số liền nhau
)
_LOOKALIKE_DIGITS_RE: Final = re.compile(r"(?<![A-Za-z0-9])[\dOoIl|]{9,}(?![A-Za-z0-9])")
_LOOKALIKE_TABLE: Final = str.maketrans("OoIl|", "00111")
_DIGIT_WORD: Final = (
    r"(?:không|một|hai|ba|bốn|tư|năm|lăm|sáu|bảy|tám|chín"
    r"|zero|one|two|three|four|five|six|seven|eight|nine)"
)
_SPOKEN_ITEM: Final = rf"(?:{_DIGIT_WORD}|\d+)"
_SPOKEN_RUN_RE: Final = re.compile(
    rf"(?<!\w){_SPOKEN_ITEM}(?:[\s,.\-]+{_SPOKEN_ITEM})+(?!\w)", re.IGNORECASE
)
_SPOKEN_MIN_DIGITS: Final = 7


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    text: str
    removed: tuple[str, ...]  # loại đã tước: url|email|phone|template|handle|encoded


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def sanitize_outbound(text: str, *, whitelist_hotlines: Iterable[str] = ()) -> SanitizeResult:
    """Strict Outbound Sanitizer. ``whitelist_hotlines``: số chính thức (cấu hình cứng) — nếu bản
    nháp chứa đúng số đó thì thay bằng ``{{OFFICIAL_HOTLINE}}`` (không để số rõ trong văn bản)."""
    allowed = {d for h in whitelist_hotlines if (d := _digits(h))}
    removed: list[str] = []
    out = unicodedata.normalize("NFKC", text)
    out = out.translate(_DOT_TABLE).translate(_HOMOGLYPH_TABLE)
    out = _ZERO_WIDTH_RE.sub("", out)
    out = out.replace(OFFICIAL_HOTLINE_VAR, _SENTINEL)

    def strip_templates(m: re.Match[str]) -> str:
        removed.append("template")
        return ""

    out = _TEMPLATE_RE.sub(strip_templates, out)
    out = _OBF_SCHEME_RE.sub(lambda m: f"http{m.group(1)}://", out)
    out = _OBF_DOT_RE.sub(".", out)
    out = _OBF_AT_RE.sub("@", out)

    def strip_as(kind: str) -> Callable[[re.Match[str]], str]:
        def _strip(m: re.Match[str]) -> str:
            removed.append(kind)
            return " "

        return _strip

    def fix_lookalike(m: re.Match[str]) -> str:
        run = m.group(0)  # "09l2345678", "O912345678": chữ giả dạng chữ số
        real = sum(c.isdigit() for c in run)
        return run.translate(_LOOKALIKE_TABLE) if real >= 6 else run

    out = _LOOKALIKE_DIGITS_RE.sub(fix_lookalike, out)
    out = _EMAIL_RE.sub(strip_as("email"), out)
    out = _HANDLE_RE.sub(strip_as("handle"), out)
    out = _URL_RE.sub(strip_as("url"), out)
    out = _ENCODED_RE.sub(strip_as("encoded"), out)

    def strip_phone(m: re.Match[str]) -> str:
        digits = _digits(m.group(0))
        if digits in allowed or ("84" + digits[1:] in allowed if digits.startswith("0") else False):
            return _SENTINEL
        removed.append("phone")
        return " "

    for pattern in _PHONE_RES:
        out = pattern.sub(strip_phone, out)

    out = _IP_RE.sub(strip_as("url"), out)  # sau SĐT: "1.8.0.0.5.8.8.8" là hotline, không phải IP

    def strip_spoken(m: re.Match[str]) -> str:
        run = m.group(0)
        words = re.findall(_DIGIT_WORD, run, flags=re.IGNORECASE)
        total = len(words) + len(_digits(run))
        if words and total >= _SPOKEN_MIN_DIGITS:  # chữ số đọc thành lời (có thể trộn số)
            removed.append("phone")
            return " "
        return run

    out = _SPOKEN_RUN_RE.sub(strip_spoken, out)
    out = out.replace("@", " ")  # allowlist: không còn '@' nào ngoài kết quả đã lọc
    out = re.sub(r"[ 	]{2,}", " ", out).replace(" ,", ",").replace(" .", ".").strip()
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
