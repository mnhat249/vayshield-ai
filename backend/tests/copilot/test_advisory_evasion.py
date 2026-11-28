"""EC-AI21 (review T15): sanitizer không bị lách bằng chèn dấu, homoglyph, dấu chấm Unicode..."""

from __future__ import annotations

import re

import pytest

from app.copilot.advisory import OFFICIAL_HOTLINE_VAR, is_outbound_safe, sanitize_outbound

LINK_CASES = [
    "Truy cập zalo . me/abc ngay",
    "Truy cập zalo(dot)me/abc ngay",
    "Truy cập zalo[dot]me/abc ngay",
    "Truy cập zalo{dot}me/abc ngay",
    "Truy cập zalo (.) me/abc ngay",
    "Truy cập zalo。me/abc ngay",
    "Truy cập zalo｡me/abc ngay",
    "Truy cập zalo.mе/abc ngay",  # 'е' Cyrillic
    "Truy cập zаlo.me/abc ngay",  # 'а' Cyrillic
    "Truy cập vay-nhanh.việt ngay",
    "Truy cập t.me/abc ngay",
    "Truy cập hxxps[:]//evil[.]com/abc ngay",
    "Mở 10.0.0.1:8080/abc ngay",
    "Mở http://192.168.1.1/abc ngay",
    "Mở aHR0cHM6Ly96YWxvLm1lL2FiYw== ngay",
]
PHONE_CASES = [
    "Gọi 091/234/5678 ngay",
    "Gọi 091_234_5678 ngay",
    "Gọi 091-234-5678 ngay",
    "Gọi 0912.345.678 ngay",
    "Gọi 0912 345 678 ngay",
    "Gọi 0⁣912345678 ngay",  # ký tự vô hình U+2063
    "Gọi 09l2345678 ngay",  # chữ l giả dạng 1
    "Gọi O912345678 ngay",  # chữ O giả dạng 0
    "Gọi ０９１２３４５６７８ ngay",
    "Gọi +84 912 345 678 ngay",
    "hotline 1 9 0 0 1 2 3 4",
    "hotline 1900 1234",
    "hotline 1.8.0.0.5.8.8.8",
    "Gọi không chín 1 2 3 4 5 6 7 8",
    "Gọi chín không một hai ba bốn năm sáu bảy",
    "Gọi 09123456789012",
]
HANDLE_CASES = [
    "Telegram @scam_support_vn",
    "Zalo @scam.support",
    "abc (at) gmail (dot) com",
    "abc [at] gmail [dot] com",
    "email lua @ gmail.com",
    "abc@gmail.com",
]


@pytest.mark.parametrize("text", LINK_CASES)
def test_ec_ai21_review_link_obfuscation_stripped(text: str) -> None:
    r = sanitize_outbound(text)
    assert "abc" not in r.text and "evil" not in r.text and "10.0.0.1" not in r.text, r.text
    assert "192.168" not in r.text and "việt" not in r.text and "zalo" not in r.text.lower()
    assert r.removed
    assert is_outbound_safe(r.text)


@pytest.mark.parametrize("text", PHONE_CASES)
def test_ec_ai21_review_phone_obfuscation_stripped(text: str) -> None:
    r = sanitize_outbound(text)
    assert not re.search(r"\d", r.text), r.text
    assert "phone" in r.removed
    assert is_outbound_safe(r.text)


@pytest.mark.parametrize("text", HANDLE_CASES)
def test_ec_ai21_review_handles_and_obfuscated_email_stripped(text: str) -> None:
    r = sanitize_outbound(text)
    assert "@" not in r.text and "scam" not in r.text and "gmail" not in r.text, r.text
    assert r.removed


def test_ec_ai21_review_allowlist_no_digit_run_or_at_outside_sentinel() -> None:
    r = sanitize_outbound(
        f"Hotline {OFFICIAL_HOTLINE_VAR}, @x_y_z 24 giờ, 1.200.000 đồng, 12345678"
    )
    assert OFFICIAL_HOTLINE_VAR in r.text and "@" not in r.text
    assert not re.search(r"\d{7,}", r.text)
    assert "24 giờ" in r.text  # số ngắn hợp lệ không bị tước


def test_ec_ai21_review_legit_vietnamese_text_is_untouched() -> None:
    text = "Tuyệt đối không cung cấp mã OTP. Trong 24 giờ qua có 3 sự cố, hãy cẩn thận."
    assert sanitize_outbound(text).text == text
