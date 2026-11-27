"""Vietnamese TTS Verbalizer (EC-AI26): đổi số tiền, ký hiệu, viết tắt pháp lý thành chữ tự nhiên
trước khi đưa vào bộ phát âm. Hàm thuần, không I/O.

Ví dụ: ``12.600.000 VND`` -> ``mười hai triệu sáu trăm nghìn đồng``; ``NHNN`` -> ``Ngân hàng Nhà
nước``; số có số 0 đầu / dãy rất dài (SĐT, hotline) đọc từng chữ số.

Giả định an toàn: dấu ``.`` giữa nhóm 3 chữ số là phân cách hàng nghìn; dấu ``,`` là dấu thập
phân (chuẩn Việt Nam) — trừ khi có hậu tố tiền tệ thì ``,ddd`` được coi là phân cách hàng nghìn.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

_DIGITS: Final = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
_SCALES: Final = ("", "nghìn", "triệu", "tỷ")
_MAX_GROUPS: Final = 7  # tới 10^21; lớn hơn thì đọc từng chữ số
_LONG_DIGITS: Final = 10  # chuỗi chữ số liền >= 10 ký tự: đọc từng chữ số (SĐT, số tài khoản)

# Viết tắt pháp lý/tài chính (khớp cả từ, phân biệt hoa thường). Cụm dài đặt trước.
_ABBREVIATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("TT-NHNN", "Thông tư của Ngân hàng Nhà nước"),
    ("NĐ-CP", "Nghị định của Chính phủ"),
    ("NHNN", "Ngân hàng Nhà nước"),
    ("BLDS", "Bộ luật Dân sự"),
    ("BLHS", "Bộ luật Hình sự"),
    ("TCTD", "tổ chức tín dụng"),
    ("CCCD", "căn cước công dân"),
    ("CIC", "Trung tâm Thông tin Tín dụng Quốc gia"),
    ("APR", "lãi suất thực tế quy theo năm"),
    ("OTP", "ô ti pi"),
    ("VNĐ", "đồng"),
    ("VND", "đồng"),
)

_DATE_RE: Final = re.compile(r"(?<![\d/])(?:ngày\s+)?(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])")
_HOTLINE_RE: Final = re.compile(r"(?<!\d)1[89]00(?:[ .]?\d{3,4}){1,2}(?!\d)")
_UNIT_RE: Final = (
    r"(?P<unit>\s*(?:%|₫|đ(?![\wÀ-ỹ])|tr(?![\wÀ-ỹ])|k(?![\wÀ-ỹ])|(?i:vn[dđ])(?![\wÀ-ỹ])))?"
)
_NUMBER_RE: Final = re.compile(r"(?<![\d\w])(?P<num>\d(?:[\d.,]*\d)?)" + _UNIT_RE)
_PER_RE: Final = re.compile(r"\s*/\s*(năm|tháng|ngày|tuần)\b")
_THOUSANDS_DOT: Final = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_THOUSANDS_COMMA: Final = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_SPACES_RE: Final = re.compile(r"[ \t]+")
_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _read_group(n: int, full: bool) -> str:
    """Đọc một nhóm 0..999; ``full`` = có nhóm cao hơn đứng trước (đọc "không trăm", "linh")."""
    hundreds, rest = divmod(n, 100)
    tens, unit = divmod(rest, 10)
    parts: list[str] = []
    if hundreds or full:
        parts.append(f"{_DIGITS[hundreds]} trăm")
    if tens == 0:
        if unit:
            if hundreds or full:
                parts.append("linh")
            parts.append(_DIGITS[unit])
    elif tens == 1:
        parts.append("mười")
        if unit == 5:
            parts.append("lăm")
        elif unit:
            parts.append(_DIGITS[unit])
    else:
        parts.append(f"{_DIGITS[tens]} mươi")
        if unit == 1:
            parts.append("mốt")
        elif unit == 5:
            parts.append("lăm")
        elif unit:
            parts.append(_DIGITS[unit])
    return " ".join(parts)


def digits_to_vi(digits: str) -> str:
    """Đọc từng chữ số (SĐT, hotline, chuỗi có số 0 đầu)."""
    return " ".join(_DIGITS[int(c)] for c in digits if c.isdigit())


def number_to_vi(n: int) -> str:
    """Đọc số nguyên không âm theo quy tắc tiếng Việt."""
    if n < 0:
        raise ValueError("chỉ hỗ trợ số không âm")
    if n == 0:
        return _DIGITS[0]
    groups: list[int] = []
    rest = n
    while rest:
        rest, g = divmod(rest, 1000)
        groups.append(g)
    if len(groups) > _MAX_GROUPS:
        return digits_to_vi(str(n))
    out: list[str] = []
    started = False
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if g == 0:
            continue
        out.append(_read_group(g, full=started))
        scale = _scale_name(idx)
        if scale:
            out.append(scale)
        started = True
    return " ".join(out)


def _scale_name(idx: int) -> str:
    """idx 0:'' 1:nghìn 2:triệu 3:tỷ 4:nghìn tỷ 5:triệu tỷ 6:tỷ tỷ."""
    if idx < len(_SCALES):
        return _SCALES[idx]
    return f"{_SCALES[idx - 3]} tỷ"


def _split_number(token: str, currency: bool) -> tuple[str, str] | None:
    """Tách ``token`` thành (phần nguyên chỉ gồm chữ số, phần thập phân); None nếu không rõ."""
    has_dot, has_comma = "." in token, "," in token
    if has_dot and has_comma:
        last = max(token.rfind("."), token.rfind(","))
        sep = token[last]
        whole, frac = token[:last], token[last + 1 :]
        other = "," if sep == "." else "."
        if sep in whole or not frac.isdigit():
            return None
        return whole.replace(other, ""), frac
    if has_dot:
        if _THOUSANDS_DOT.match(token):
            return token.replace(".", ""), ""
        if token.count(".") == 1:
            whole, frac = token.split(".")
            return whole, frac
        return None
    if has_comma:
        if _THOUSANDS_COMMA.match(token) and (currency or token.count(",") > 1):
            return token.replace(",", ""), ""
        if token.count(",") == 1:
            whole, frac = token.split(",")
            return whole, frac
        return None
    return token, ""


def _verbalize_number(token: str, unit: str | None) -> str:
    unit_clean = (unit or "").strip()
    currency = unit_clean.lower() in {"₫", "đ", "vnđ", "vnd"}
    parts = _split_number(token, currency)
    if parts is None:  # định dạng lạ: đọc từng chữ số cho an toàn
        spoken = digits_to_vi(token)
        frac = ""
    else:
        whole, frac = parts
        plain = "," not in token and "." not in token
        if (plain and len(whole) > 1 and whole.startswith("0")) or (
            plain and len(whole) >= _LONG_DIGITS
        ):
            spoken = digits_to_vi(whole)  # SĐT, số tài khoản, mã số
        else:
            spoken = number_to_vi(int(whole))
    if frac:
        spoken += " phẩy " + digits_to_vi(frac)
    low = unit_clean.lower()
    if low == "%":
        spoken += " phần trăm"
    elif low in {"₫", "đ", "vnđ", "vnd"}:
        spoken += " đồng"
    elif low == "tr":
        spoken += " triệu"
    elif low == "k":
        spoken += " nghìn"
    return spoken


def normalize_vi(text: str) -> str:
    """Chuẩn hóa văn bản tiếng Việt để đọc thành tiếng. Idempotent với văn bản đã chuẩn hóa."""
    out = unicodedata.normalize("NFC", text)
    out = _CONTROL_RE.sub(" ", out)
    out = _HOTLINE_RE.sub(lambda m: digits_to_vi(m.group(0)), out)
    out = _DATE_RE.sub(
        lambda m: (
            f"ngày {number_to_vi(int(m.group(1)))} tháng {number_to_vi(int(m.group(2)))}"
            f" năm {number_to_vi(int(m.group(3)))}"
        ),
        out,
    )
    out = _PER_RE.sub(lambda m: f" mỗi {m.group(1)}", out)
    for short, spoken in _ABBREVIATIONS:
        out = re.sub(rf"(?<![\w]){re.escape(short)}(?![\w])", spoken, out)
    out = _NUMBER_RE.sub(lambda m: _verbalize_number(m.group("num"), m.group("unit")), out)
    return _SPACES_RE.sub(" ", out).strip()
