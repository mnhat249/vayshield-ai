"""Copilot B2B (File 3 §5.2): Advisory Writer + Strict Outbound Sanitizer (EC-AI21).

Nguyên tắc TỪ CHỐI-THAY-VÌ-TƯỚC (đảo hướng sau 3 vòng vá danh sách chặn vẫn bị lách): bản nháp
cảnh báo chỉ được ghép từ MẪU CỨNG + biến whitelist (``{{OFFICIAL_HOTLINE}}``, tên bên cho vay đã
kiểm). Mọi văn bản KHÔNG tin cậy (LLM, nội dung ảnh, tên bên cho vay) phải qua kiểm allowlist:

- ký tự chỉ gồm chữ cái Latin (kể cả tiếng Việt), chữ số ASCII, khoảng trắng/xuống dòng và dấu câu
  ASCII cơ bản ``. , ; : ! ? - ( ) ' "`` (``%``, ``/``, ``₫`` chỉ hợp lệ bên trong số tiền/ngày/
  phần trăm/số hiệu văn bản đã nhận diện);
- số tiền (có hậu/tiền tố tiền tệ hoặc định dạng nghìn chuẩn ``1.200.000``) được nhận diện TRƯỚC và
  thay bằng placeholder để không bị coi là SĐT/IP/hotline/URL;
- gặp bất kỳ dấu hiệu nghi ngờ nào (ký tự lạ, ``@``, dãy >= 7 chữ số không phải tiền, chữ số đọc
  thành lời, từ ``chấm``/``dot``, TLD, IP, base64, handle...) thì LOẠI CẢ ĐOẠN và quay về mẫu cứng
  — không cố tước từng phần.

Số hotline chính thức KHÔNG bao giờ được viết trong văn bản: chỉ chèn biến mẫu để nhân sự điền từ
bảng cấu hình cứng. Hàm kiểm là thuần, không log nội dung.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final, Protocol

from app.core.schemas.ops import ScamTrap, SeverityLevel

OFFICIAL_HOTLINE_VAR: Final = "{{OFFICIAL_HOTLINE}}"
MIN_USEFUL_CHARS: Final = 40
MAX_LENDER_NAME_LEN: Final = 60

_HOTLINE_SENTINEL: Final = "\ue002"  # biến hotline chính thức (private-use: input có sẽ bị loại)
_MONEY_PH: Final = "\ue000"
_SAFE_PH: Final = "\ue001"
_PLACEHOLDERS: Final = frozenset({_MONEY_PH, _SAFE_PH, _HOTLINE_SENTINEL})
_ALLOWED_PUNCT: Final = frozenset(".,;:!?-()'\"%/₫")
_MAX_WORD_LEN: Final = 25
_MAX_MIXED_CASE_WORD_LEN: Final = 16
_MAX_PHONE_RUN: Final = 7  # >= 7 chữ số (kể cả đọc thành lời) liên tiếp = SĐT/tài khoản

# --- số tiền / ngày / % / số hiệu văn bản: nhận diện TRƯỚC, đổi thành placeholder ---------------
_NUM: Final = r"(?:[1-9]\d{0,2}(?:[.,]\d{3})+|[1-9]\d*|0)(?:[.,]\d{1,2})?"
_CUR: Final = r"(?:đồng|đ|₫|vnđ|vnd)"
_UNIT: Final = rf"(?:(?:triệu|trieu|tỷ|ty|nghìn|nghin|ngàn|ngan|tr|k)(?:\s?{_CUR})?|{_CUR})"
_PER: Final = r"(?:/(?:năm|tháng|ngày|tuần|lần|người|kỳ))?"
_MONEY_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(rf"(?<![\w.,]){_NUM}\s?{_UNIT}(?!\w){_PER}", re.IGNORECASE),
    re.compile(rf"(?<![\w.,])(?:vnđ|vnd|₫)\s?{_NUM}(?![\w]|[.,]\d){_PER}", re.IGNORECASE),
    # định dạng nghìn chuẩn không hậu tố: 1.200.000, 1.900.000.000, 84.000.000.000
    re.compile(
        rf"(?<![\w.,])[1-9]\d{{0,2}}(?:\.\d{{3}}){{2,}}(?:,\d{{1,2}})?(?![\w]|[.,]\d){_PER}"
    ),
)
_DATE_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?<![\w/])(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}(?![\w/])"),
    re.compile(r"(?<![\w/])(?:0?[1-9]|1[0-2])/(?:19|20)\d{2}(?![\w/])"),
    re.compile(r"(?<![\w/])(?:0?[1-9]|[12]\d|3[01])/(?:0?[1-9]|1[0-2])(?![\w/])"),
)
_PERCENT_RE: Final = re.compile(
    r"(?<![\w.,])\d{1,3}(?:[.,]\d{1,2})?\s?%(?:/(?:năm|tháng|ngày|tuần))?(?!\w)"
)
_LEGAL_RE: Final = re.compile(r"(?<![\w/])\d{1,3}/(?:19|20)\d{2}/[A-ZĐ]{2,}(?:-[A-ZĐ]{2,})*(?!\w)")
_IP_RE: Final = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?!\d)")

# --- dấu hiệu nghi ngờ sau khi đã bảo vệ số tiền -----------------------------------------------
_TOKEN_RE: Final = re.compile(r"(\d+)|([^\W\d_]+)|[\ue000-\ue002]")
_WORD_RE: Final = re.compile(r"[^\W_]+")
_SPACE_BEFORE_PUNCT_RE: Final = re.compile(r"[ \n][.,;:]")
_LETTER_PUNCT_LETTER_RE: Final = re.compile(r"[^\W\d_][.,;:][^\W\d_]")
_LOWER_AFTER_STOP_RE: Final = re.compile(r"(?<![.])[.!?]\s+(?=[^\W\d_])")
_CONTACT_RE: Final = re.compile(
    r"\b(?:tg|telegram|zalo|fb|facebook|ig|insta|instagram|tiktok|viber|whatsapp|wechat|skype"
    r"|kakao|kakaotalk)\s*:",  # "Zalo: <định danh>" = đưa kênh liên hệ
    re.IGNORECASE,
)
_SPOKEN_AT_RE: Final = re.compile(r"\ba\s+(?:cong|co\s?ng|at)\b|\b(?:gach|dau)\s+(?:cheo|cham)\b")
_DIGIT_WORDS: Final = frozenset(
    [
        "khong",
        "mot",
        "hai",
        "ba",
        "bon",
        "tu",
        "nam",
        "lam",
        "sau",
        "bay",
        "tam",
        "chin",
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
    ]
)
_BAD_WORDS: Final = frozenset(
    [
        "cham",
        "dot",
        "slash",
        "gmail",
        "yahoo",
        "hotmail",
        "outlook",
        "icloud",
        "http",
        "https",
        "hxxp",
        "hxxps",
        "www",
    ]
)
_TLD_WORDS: Final = frozenset(
    [
        "com",
        "net",
        "org",
        "vn",
        "xyz",
        "info",
        "biz",
        "io",
        "ly",
        "cc",
        *(["tk", "ml", "tv", "ws", "gl", "gd"]),
    ]
)


@dataclass(frozen=True, slots=True)
class SanitizeResult:
    text: str  # văn bản đã kiểm (nguyên vẹn); RỖNG nếu bị loại
    removed: tuple[str, ...]  # lý do loại: charset|url|phone|template|handle|encoded|obfuscation
    rejected: bool = False


def _digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _strip_accents(s: str) -> str:
    decomposed = unicodedata.normalize("NFD", s.casefold())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _reject(reason: str) -> SanitizeResult:
    return SanitizeResult(text="", removed=(reason,), rejected=True)


def _charset_ok(text: str) -> bool:
    for c in text:
        if c in " \n" or c in _ALLOWED_PUNCT or c in _PLACEHOLDERS:
            continue
        if c.isascii() and c.isdigit():
            continue
        if unicodedata.category(c).startswith("L") and "LATIN" in unicodedata.name(c, ""):
            continue
        return False
    return True


def _hotline_regex(number: str) -> re.Pattern[str] | None:
    digits = _digits(number)
    if len(digits) < 4:
        return None
    prefix = ""
    if digits.startswith("0"):
        digits, prefix = digits[1:], r"(?:\+\s?84[ .\-]?|84[ .\-]?|0)"
    body = r"[ .\-]?".join(re.escape(d) for d in digits)
    return re.compile(rf"(?<![\d]){prefix}{body}(?!\d)")


def _is_money_like_ip(parts: tuple[str, ...]) -> bool:
    return parts[2] == "000" and parts[3] == "000"  # 1.200.000.000 / 84.000.000.000


def _protect(text: str) -> str:
    out = text
    for pattern in _DATE_RES:
        out = pattern.sub(_SAFE_PH, out)
    out = _LEGAL_RE.sub(_SAFE_PH, out)
    out = _PERCENT_RE.sub(_SAFE_PH, out)
    for pattern in _MONEY_RES:
        out = pattern.sub(_MONEY_PH, out)
    return out


def _digit_run_too_long(text: str) -> bool:
    """Dãy chữ số/số đọc thành lời liên tiếp (ngăn bằng khoảng trắng/dấu câu) >= 7 chữ số."""
    run = 0
    for m in _TOKEN_RE.finditer(text):
        digits, word = m.group(1), m.group(2)
        if digits:
            run += len(digits)
        elif word and _strip_accents(word) in _DIGIT_WORDS:
            if _strip_accents(word) == "nam" and run == 0:
                continue  # "năm" (year) không khởi đầu dãy: "Năm 2024"
            run += 1
        else:
            run = 0
        if run >= _MAX_PHONE_RUN:
            return True
    return False


def _words_suspicious(text: str) -> str | None:
    for w in _WORD_RE.findall(text):
        letters = [c for c in w if c.isalpha()]
        if letters and len(letters) != len(w):
            return "obfuscation"  # trộn chữ + số (kh0ng, d0t, O912...)
        low, bare = w.casefold(), _strip_accents(w)
        if len(w) > _MAX_WORD_LEN:
            return "encoded"
        if (
            len(w) >= _MAX_MIXED_CASE_WORD_LEN
            and any(c.isupper() for c in w[1:])
            and any(c.islower() for c in w)
        ):
            return "encoded"  # base64 chỉ chữ cái
        if "dot" in low or bare == "cham" or (bare in _BAD_WORDS) or low in _BAD_WORDS:
            return "url"
        if bare in _TLD_WORDS and w != "VN":
            return "url"
    return None


def sanitize_outbound(text: str, *, whitelist_hotlines: Iterable[str] = ()) -> SanitizeResult:
    """Kiểm allowlist văn bản không tin cậy. Hợp lệ -> trả nguyên văn (số hotline whitelist đổi
    thành ``{{OFFICIAL_HOTLINE}}``); có BẤT KỲ dấu hiệu nghi ngờ -> ``rejected`` và ``text`` rỗng
    (người gọi phải dùng mẫu cứng, không dùng một phần văn bản)."""
    raw = unicodedata.normalize("NFC", text)
    if any(unicodedata.category(c) == "Co" for c in raw):
        return _reject("charset")  # private-use trùng placeholder nội bộ
    out = raw.replace(OFFICIAL_HOTLINE_VAR, _HOTLINE_SENTINEL)
    for number in whitelist_hotlines:
        pattern = _hotline_regex(number)
        if pattern is not None:
            out = pattern.sub(_HOTLINE_SENTINEL, out)
    if "{" in out or "}" in out or "@" in out:
        return _reject("template" if "{" in out or "}" in out else "handle")
    if not _charset_ok(out):
        return _reject("charset")
    for m in _IP_RE.finditer(out):
        parts = m.groups()
        if all(int(p) <= 255 for p in parts) and not _is_money_like_ip(parts):
            return _reject("url")
    checked = out
    out = _protect(out)
    if "/" in out or "%" in out or "₫" in out:
        return _reject("url")
    if _digit_run_too_long(out):
        return _reject("phone")
    if "xn--" in out.casefold():
        return _reject("url")
    why = _words_suspicious(out)
    if why is not None:
        return _reject(why)
    bare = _strip_accents(out)
    if (
        _SPACE_BEFORE_PUNCT_RE.search(out)
        or _LETTER_PUNCT_LETTER_RE.search(out)
        or _SPOKEN_AT_RE.search(bare)
        or _CONTACT_RE.search(out)
    ):
        return _reject("obfuscation")
    for m in _LOWER_AFTER_STOP_RE.finditer(out):
        if out[m.end()].islower():
            return _reject("obfuscation")  # "vaynhanh. com": sau dấu chấm câu phải viết hoa
    return SanitizeResult(
        text=checked.replace(_HOTLINE_SENTINEL, OFFICIAL_HOTLINE_VAR).strip(), removed=()
    )


def is_outbound_safe(text: str) -> bool:
    """True nếu ``text`` vượt kiểm allowlist và không đổi sau khi kiểm."""
    result = sanitize_outbound(text)
    return not result.rejected and result.text == text.strip()


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
        "đang xuất hiện cuộc gọi {actor} tự xưng nhân viên nhắc nợ hoặc thu hồi khoản vay, yêu cầu "
        "chuyển tiền vào tài khoản cá nhân. Hãy kiểm tra khoản vay trong ứng dụng chính thức."
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


def _safe_lender(name: str | None) -> str | None:
    """Tên bên cho vay chỉ được dùng nếu vượt kiểm allowlist (chữ cái/khoảng trắng, ngắn)."""
    if not name or len(name) > MAX_LENDER_NAME_LEN:
        return None
    result = sanitize_outbound(name)
    if result.rejected or any(not (c.isalpha() or c == " ") for c in result.text):
        return None
    return result.text


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
            if not clean.rejected and len(clean.text) >= MIN_USEFUL_CHARS:
                return AdvisoryDraft(clean.text, used_llm=True, sanitized_removed=())
            # Bị loại (hoặc quá ngắn): bỏ CẢ đoạn LLM, không dùng một phần.
            removed = clean.removed
        # Tên bên cho vay (danh bạ) cũng là văn bản không tin cậy: không qua kiểm -> dùng chung.
        lender = _safe_lender(ctx.lender_name)
        if ctx.lender_name and lender is None:
            removed += ("lender_name",)
        text = _template_draft(replace(ctx, lender_name=lender))
        final = sanitize_outbound(text, whitelist_hotlines=self._whitelist)
        if final.rejected:  # không thể xảy ra với mẫu cứng; phòng thủ chiều sâu
            text = _template_draft(replace(ctx, lender_name=None))
            final = sanitize_outbound(text)
            if final.rejected:
                text = f"Cảnh báo lừa đảo vay tiền. Liên hệ tổng đài: {OFFICIAL_HOTLINE_VAR}."
                final = SanitizeResult(text=text, removed=())
        return AdvisoryDraft(final.text, used_llm=False, sanitized_removed=removed)


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
