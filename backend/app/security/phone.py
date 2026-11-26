"""Chuẩn hóa SĐT về ITU-T E.164 nghiêm ngặt trước khi băm blind index (EC-DB10).

``0909123456`` / ``+84909123456`` / ``84909123456`` / ``840909123456`` / ``+84 (0)909.123.456``
→ ``+84909123456``. Đầu số di động cũ 11 số (chuyển đổi 2018, vd ``01662xxxxxx``) được ánh xạ
sang đầu số mới 10 số để không lệch HMAC giữa đối tác B2B và thiết bị.

Thông điệp lỗi KHÔNG BAO GIỜ chứa số điện thoại đầu vào (Luật 91/2025, không log PII).
"""

from __future__ import annotations

import re
from typing import Final

VN_COUNTRY_CODE: Final = "84"

# Bảng chuyển đầu số di động 11 → 10 số (Bộ TT&TT, 2018). Khóa: 4 chữ số đầu dạng nội địa.
LEGACY_MOBILE_PREFIXES: Final[dict[str, str]] = {
    # Viettel
    "0162": "032",
    "0163": "033",
    "0164": "034",
    "0165": "035",
    "0166": "036",
    "0167": "037",
    "0168": "038",
    "0169": "039",
    # MobiFone
    "0120": "070",
    "0121": "079",
    "0122": "077",
    "0126": "076",
    "0128": "078",
    # VinaPhone
    "0123": "083",
    "0124": "084",
    "0125": "085",
    "0127": "081",
    "0129": "082",
    # Vietnamobile
    "0186": "056",
    "0188": "058",
    # Gmobile
    "0199": "059",
}

_SEPARATORS_RE: Final = re.compile(r"[\s.\-()/]")
_DIGITS_RE: Final = re.compile(r"\d+")
# Di động VN sau mã quốc gia: 9 chữ số, đầu 3/5/7/8/9.
_VN_MOBILE_NSN_RE: Final = re.compile(r"[35789]\d{8}")
_E164_RE: Final = re.compile(r"\+[1-9]\d{7,14}")


class PhoneNormalizationError(ValueError):
    """SĐT không chuẩn hóa được. Không chứa giá trị đầu vào."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Số điện thoại không hợp lệ: {reason}")


def _vn_national_to_e164(national: str) -> str:
    """``national`` bắt đầu bằng 0 (dạng nội địa)."""
    if len(national) == 11 and national[:4] in LEGACY_MOBILE_PREFIXES:
        national = LEGACY_MOBILE_PREFIXES[national[:4]] + national[4:]
    if len(national) != 10:
        raise PhoneNormalizationError("độ dài không đúng số di động Việt Nam")
    nsn = national[1:]
    if not _VN_MOBILE_NSN_RE.fullmatch(nsn):
        raise PhoneNormalizationError("không phải đầu số di động Việt Nam")
    return "+" + VN_COUNTRY_CODE + nsn


def normalize_phone_e164(raw: str) -> str:
    """Chuẩn hóa SĐT về E.164. SĐT VN phải là di động hợp lệ; SĐT quốc tế giữ nguyên dạng E.164.

    Raises:
        PhoneNormalizationError: định dạng không nhận diện được.
    """
    if not isinstance(raw, str):
        raise PhoneNormalizationError("kiểu dữ liệu không hợp lệ")
    text = _SEPARATORS_RE.sub("", raw.strip())
    if text.startswith("00"):  # tiền tố quay số quốc tế
        text = "+" + text[2:]
    has_plus = text.startswith("+")
    digits = text[1:] if has_plus else text
    if not digits or not _DIGITS_RE.fullmatch(digits):
        raise PhoneNormalizationError("chứa ký tự không phải chữ số")
    if len(digits) > 15:
        raise PhoneNormalizationError("quá dài")

    if digits.startswith(VN_COUNTRY_CODE) and (has_plus or len(digits) >= 11):
        # +84 909..., 84 909..., +84 0909... (số 0 thừa), 84 0909..., +84 1662... (đầu số cũ)
        rest = digits[len(VN_COUNTRY_CODE) :]
        national = rest if rest.startswith("0") else "0" + rest
        return _vn_national_to_e164(national)
    if not has_plus:
        if digits.startswith("0"):
            return _vn_national_to_e164(digits)
        raise PhoneNormalizationError("thiếu mã quốc gia")
    candidate = "+" + digits
    if not _E164_RE.fullmatch(candidate):
        raise PhoneNormalizationError("không đúng E.164")
    return candidate
