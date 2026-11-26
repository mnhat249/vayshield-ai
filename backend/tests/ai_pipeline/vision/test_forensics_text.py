"""EC-AI06, EC-AI23, EC-AI25, S2-EC19."""

from __future__ import annotations

import io
import unicodedata

import numpy as np
import pytest
from PIL import Image

from ai_pipeline.vision import canonicalize_text, receipt_forensics
from ai_pipeline.vision.text import fold_diacritics


def test_ec_ai06_napas_247_stuck_with_fee_is_flagged() -> None:
    text = (
        "Chuyển khoản nhanh 24/7 thành công. Giao dịch bị treo tại cổng trung gian, "
        "vui lòng nộp 10% phí giải tỏa để người nhận nhận được tiền."
    )
    r = receipt_forensics(text)
    assert r.fake_receipt_napas_impossible and r.is_suspected_fake
    assert "FAKE_RECEIPT_NAPAS_IMPOSSIBLE" in r.reasons


def test_ec_ai06_obfuscated_text_still_flagged() -> None:
    text = "CK nhanh 24/7 th​ành công nhưng bị t.r.e.o, nộp ph.í mở băng 10%"
    assert receipt_forensics(text).fake_receipt_napas_impossible


@pytest.mark.parametrize(
    "text",
    [
        "Chuyển khoản nhanh 24/7 thành công. Số tiền 1.500.000 VND. Phí 0 đ.",
        "Giao dịch thành công. Người nhận: NGUYEN VAN A",
        "Chuyển khoản nhanh 24/7 đang xử lý",  # đang xử lý nhưng không đòi phí
        "",
    ],
)
def test_ec_ai06_legit_receipts_not_flagged(text: str) -> None:
    assert not receipt_forensics(text).fake_receipt_napas_impossible


def _noisy_jpeg_base() -> Image.Image:
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(base).save(buf, format="JPEG", quality=60)
    buf.seek(0)
    img = Image.open(buf)
    img.load()
    return img.convert("RGB")


def test_ec_ai06_ela_flags_pasted_region_near_amount() -> None:
    """EC-AI06: vùng số tiền dán vào có lịch sử nén khác nền → ELA bất thường."""
    img = _noisy_jpeg_base()
    rng = np.random.default_rng(11)
    patch = rng.integers(0, 255, (60, 160, 3), dtype=np.uint8)
    img.paste(Image.fromarray(patch), (100, 120))
    r = receipt_forensics(None, img, (100, 120, 260, 180))
    assert r.ela_suspicious and r.ela_ratio is not None and r.ela_ratio >= 3.0
    assert "ELA_ANOMALY_NEAR_AMOUNT" in r.reasons


def test_ec_ai06_ela_clean_image_not_flagged() -> None:
    img = _noisy_jpeg_base()
    r = receipt_forensics(None, img, (100, 120, 260, 180))
    assert not r.ela_suspicious


def test_ec_ai23_nfd_mixed_with_nfc_unified_to_nfc() -> None:
    """EC-AI23: NFD trộn NFC → cùng một chuỗi dựng sẵn."""
    nfc = "phí mở băng tài khoản"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc
    assert canonicalize_text(nfd) == canonicalize_text(nfc) == nfc
    head, _, tail = nfc.partition(" ")
    mixed = unicodedata.normalize("NFD", head) + " " + tail
    assert canonicalize_text(mixed) == nfc


def test_ec_ai23_fullwidth_and_compat_chars_folded() -> None:
    assert canonicalize_text("ＬãI ｓuất １５％") == "LãI suất 15%"


def test_s2_ec19_dotted_letters_joined() -> None:
    """S2-EC19: "p.h.í m.ở b.ă.n.g" → "phí mở băng"."""
    assert canonicalize_text("p.h.í m.ở b.ă.n.g") == "phí mở băng"
    assert canonicalize_text("p-h-í m_ở b*ă*n*g") == "phí mở băng"


def test_s2_ec19_zero_width_removed() -> None:
    assert canonicalize_text("ph​í﻿ m‏ở") == "phí mở"


def test_s2_ec19_cyrillic_homoglyphs_mapped() -> None:
    assert canonicalize_text("рhí mở bаng") == "phí mở bang"  # р, а Cyrillic


def test_s2_ec19_leet_inside_words_only() -> None:
    assert fold_diacritics("tài kh0ản kỹ thuật") == "tai khoan ky thuat"
    assert fold_diacritics("kh1 vay") == "kh1 vay"  # chữ số cuối từ không phải giữa 2 chữ cái
    assert canonicalize_text("vay 3tr lãi 1.5%/tháng, 100k") == "vay 3tr lãi 1.5%/tháng, 100k"


def test_s2_ec19_canonicalize_does_not_corrupt_amounts_or_codes() -> None:
    """Review: canonicalize_text không áp leet -> số tiền/mã giữ nguyên."""
    assert canonicalize_text("1tr5k") == "1tr5k"
    assert canonicalize_text("HD1A3B") == "HD1A3B"


def test_ec_ai06_pending_receipt_with_free_fee_not_flagged() -> None:
    """EC-AI06 (review): 'đồng'/'Miễn phí' không phải đòi phí giải tỏa."""
    text = "Số tiền: 500.000 đồng. Phí giao dịch: Miễn phí. Trạng thái: Đang chờ xử lý"
    assert not receipt_forensics("Chuyển khoản nhanh 24/7 " + text).fake_receipt_napas_impossible
    assert not receipt_forensics(text).fake_receipt_napas_impossible


def test_ec_ai25_decimal_point_not_swallowed_by_canonicalizer() -> None:
    """EC-AI25: "1.5%/tháng" không bị nối thành "15%" (dấu chấm thập phân được bảo toàn)."""
    assert canonicalize_text("1.5%/tháng") == "1.5%/tháng"
    assert canonicalize_text("lãi 0.05% và 2.5 triệu") == "lãi 0.05% và 2.5 triệu"


def test_ec_ai06_ela_region_negative_indices_are_clipped() -> None:
    """EC-AI06 (review): region âm không được wrap quanh mảng."""
    from ai_pipeline.vision.forensics import ela_region_ratio

    img = Image.new("RGB", (64, 64), (200, 200, 200))
    a = ela_region_ratio(img, (-10, -10, 20, 20))
    b = ela_region_ratio(img, (0, 0, 20, 20))
    assert a == b


_STUCK_HEAD = "Chuyển khoản nhanh 24/7 NAPAS. Treo lệnh. "


@pytest.mark.parametrize(
    "tail",
    [
        "Vui lòng nạp 15% phí giải tỏa",
        "Vui lòng trả phí giải tỏa 500k",
        "Phí giải tỏa: 500.000đ",
        "Cần nộp thêm phí xác thực 300.000đ",
        "Cần nộp thuế thu nhập 2.000.000đ để nhận tiền",
        "Vui lòng gửi 10% phí giải ngân",
        "Đóng 10% phí",
        "Yêu cầu bổ sung 10% phí giải tỏa",
        "Cần nộp phí bảo hiểm khoản vay 10%",
        "Vui lòng chuyển 500k để mở băng",
    ],
)
def test_ec_ai06_advance_fee_variants_flagged(tail: str) -> None:
    """EC-AI06 (round 2): các cách đòi phí giải tỏa đều bị bắt (không hồi quy)."""
    assert receipt_forensics(_STUCK_HEAD + tail).fake_receipt_napas_impossible


def test_ec_ai06_free_text_after_noi_dung_is_ignored() -> None:
    text = "Chuyển khoản nhanh 24/7. Đang chờ xử lý. Nội dung: dong tien giai ngan khoan vay"
    assert not receipt_forensics(text).fake_receipt_napas_impossible


def test_ec_ai06_invalid_ela_region_does_not_crash() -> None:
    img = Image.new("RGB", (64, 64), (200, 200, 200))
    r = receipt_forensics(_STUCK_HEAD + "Đóng 10% phí", img, (100, 100, 200, 200))
    assert r.ela_ratio is None and "ELA_REGION_INVALID" in r.reasons
    assert r.fake_receipt_napas_impossible
