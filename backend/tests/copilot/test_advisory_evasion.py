"""EC-AI21 (review T15, vòng 3): TỪ CHỐI-THAY-VÌ-TƯỚC. Mẫu lách (>= 150) phải bị loại cả đoạn;
câu hợp lệ chứa số tiền (>= 50) phải giữ nguyên từng ký tự."""

from __future__ import annotations

import pytest

from app.copilot.advisory import (
    OFFICIAL_HOTLINE_VAR,
    AdvisoryContext,
    AdvisoryWriter,
    is_outbound_safe,
    sanitize_outbound,
)
from app.core.schemas.ops import ScamTrap, SeverityLevel

PHONE = "0912345678"
GROUPS = ("0912", "345", "678")
SEPARATORS = [
    "•", "∙", "⸱", "、", "¦", "×", "→", "❘", "°", "。", "｡", "|", "~", "*", "+", "=", "—", "–",
    "−", "‐", "‑", "‒", "⁃", "＿", "#", "^", "&", "`", "_", "_ _", "·", "․", "‧", "٫",
    "\t", "\u00a0",
    ":", ";", "'", "!", "?", '"', "-", " - ", "/", "\\", ".", ",", "\n",
]  # fmt: skip

PHONE_LIKE = [s.join(GROUPS) for s in SEPARATORS] + [s.join(PHONE) for s in SEPARATORS]
PHONE_LIKE += [
    "091 2345 678", "0912345 678", "091234 5678", "091 23 45 678", "09.12.34.56.78",
    "+84 912 345 678", "(+84) 912-345-678", "84-912-345-678", "0912\n345\n678",
    "０９１２３４５６７８", "٠٩١٢٣٤٥٦٧٨", "⓪⑨①②③④⑤⑥⑦⑧", "𝟎𝟗𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖", "09一二345678",
    "O9I2345678", "09l2345678", "kh0ng ch1n", "0⁣912345678", "1900 1234", "19001234",
    "1 9 0 0 1 2 3 4", "1.8.0.0.5.8.8.8", "1800.5888",
    "khong chin mot hai ba bon nam sau bay tam", "không chín một hai ba bốn năm sáu bảy tám",
    "KHÔNG CHÍN MỘT HAI BA BỐN NĂM SÁU BẢY TÁM", "0 chín 1 hai 3 bốn 5 sáu 7 tám",
    "zero nine one two three four five six seven eight", "chín không một hai ba bốn năm sáu bảy",
    "không chín 1 2 3 4 5 6 7 8", "09123456789012", "0x7f000001",
]  # fmt: skip

LINK_LIKE = [
    "vaynhanh•com", "vaynhanh cham com", "vaynhanh. com", "vaynhanh ,com", "vaynhanh,com/abc",
    "vaynhanh cham com", "vaynhanh chấm com", "vaynhanh (chấm) com", "vay nhanh chấm com",
    "vaynhanhDOTcom", "vaynhanh d0t com", "vaynhanh DOT com", "vaynhanh dot com",
    "vaynhanh[dot]com",
    "vaynhanh(dot)com", "vaynhanh{dot}com", "vaynhanh (.) com", "vaynhanh . com", "vaynhanh.com",
    "vaynhanh.vn", "vaynhanh.việt", "xn--vay-nhanh.vn", "t me/scamvn", "t.me/scamvn", "t。me/abc",
    "zalo me slash abc", "zalo.me/abc", "zalo . me/abc", "zalo(dot)me/abc", "zalo[dot]me/abc",
    "zalo。me/abc", "zalo.mе/abc", "zаlo.me/abc", "vaynhanh｡com", "vaynhanh⸱com", "vaynhanh ꓸ com",
    "vaynhanh．com", "vaynhanh∙com", "vaynhanh٫com", "vaynhanh۔com", "vaynhanh\u2024com",
    "vaynhanh\u3002com", "abc a còng gmail chấm com", "abc＠gmail．com", "abc﹫gmail﹒com",
    "abc @ gmail dot com", "abc [at] gmail [dot] com", "abc at gmail dot com", "abc@gmail.com",
    "abc (at) gmail (dot) com", "@scamvn", "@scam_support_vn", "Telegram @scam_support_vn",
    "telegram scam_vn_support", "tg: scamvn", "Zalo: abc", "hxxps://evil", "hxxps[:]//evil[.]com",
    "h t t p s : / / evil . com", "https\u200b://evil.com", "evil.c\u200bom", "https://evil.com/x",
    "www.evil.com", "http://192.168.1.1/x", "192.168.1.1", "10.0.0.1:8080", "10.0.0.1:8080/abc",
    "aHR0cHM6Ly96YWxvLm1lL2FiYw==", "aHR0cHM6Ly96YWxvLm1lL2FiYw", "aHJlZmV4YW1wbGVjb21hYmM",
    "{ {OFFICIAL_HOTLINE}}", "{{ OFFICIAL_HOTLINE }}", "{{EVIL_LINK}}", "{%url%}", "${url}",
    "[[link]]", "<a href=x>", "[Bấm đây](https://evil.com)", "vay-nhanh.com", "evil dot com",
    "bit.ly/abc123", "xn--vit",
]  # fmt: skip

EVASIONS = [f"Liên hệ {x} ngay" for x in PHONE_LIKE + LINK_LIKE]


def test_ec_ai21_evasion_corpus_has_at_least_150_samples() -> None:
    assert len(set(EVASIONS)) >= 150


@pytest.mark.parametrize("text", EVASIONS)
def test_ec_ai21_evasion_is_rejected_whole(text: str) -> None:
    r = sanitize_outbound(text)
    assert r.rejected and r.text == "" and r.removed, text
    assert not is_outbound_safe(text)


# --- Câu hợp lệ chứa số tiền: giữ NGUYÊN, không bị coi là SĐT/IP/hotline/URL ------------------
AMOUNTS = [
    "10000000 đồng", "10.000.000 đồng", "500.000đ", "2.000.000đ", "1.200.000.000 đồng",
    "1.900.000.000 đồng", "84.000.000.000 đồng", "12,600,000 VND", "1.200.000", "5.000.000",
    "1900000 VND", "1800000 đồng", "1000000000đ", "10000000000 VND", "3 triệu đồng", "2,5 triệu",
    "500K", "20tr", "VND 1,200", "₫1,200",
]  # fmt: skip
SENTENCES = [
    "Khoản vay {a} bị chiếm đoạt.",
    "Nạn nhân đã chuyển {a} cho đối tượng.",
    "Phí giải ngân {a}.Không ai được thu trước.",
    "Năm 2024, {a} bị lừa qua ứng dụng giả mạo.",
    "Từ năm 2023 đến năm 2024 có 12 vụ, thiệt hại {a}.",
    "Lãi 36%/năm, phí {a}, ngày 25/11/2026.",
    "Theo Nghị định 100/2024/NĐ-CP, mức phạt {a}.",
]


def _valid_sentences() -> list[str]:
    out = [s.format(a=a) for s in SENTENCES for a in AMOUNTS]
    out += [
        "Thông tư 43/2016/TT-NHNN quy định trần lãi suất.",
        "Gặp anh Năm. Một ngày, hai lần, ba năm, bốn tháng.",
        "Từ 1.000.000 đến 5.000.000 đồng.",
        "Phí 500.000đ/tháng, lãi 12,5%/năm.",
        "Tuyệt đối không cung cấp mã OTP. Có 3 sự cố trong 24 giờ qua.",
    ]
    return out


VALID = _valid_sentences()


def test_ec_ai21_valid_corpus_has_at_least_50_samples() -> None:
    assert len(VALID) >= 50


@pytest.mark.parametrize("text", VALID)
def test_ec_ai21_valid_sentences_with_money_are_unchanged(text: str) -> None:
    r = sanitize_outbound(text)
    assert not r.rejected and r.text == text, (text, r)


def test_ec_ai21_whitelisted_hotline_only_as_variable() -> None:
    r = sanitize_outbound("Gọi 1900 6868 ngay.", whitelist_hotlines=["1900 6868"])
    assert r.text == f"Gọi {OFFICIAL_HOTLINE_VAR} ngay."
    assert sanitize_outbound(
        "Gọi 1900 6868 hoặc 0912345678.", whitelist_hotlines=["19006868"]
    ).rejected


async def test_ec_ai21_writer_drops_whole_llm_draft_and_uses_template() -> None:
    class Evil:
        async def draft(self, context: AdvisoryContext) -> str:
            return (
                "Cảnh báo chiến dịch lừa đảo đang diễn ra, hãy liên hệ vaynhanh•com để được hỗ trợ."
            )

    d = await AdvisoryWriter(Evil()).draft(
        AdvisoryContext(trap=ScamTrap.TRAP_1_ADVANCE_FEE, severity=SeverityLevel.ELEVATED)
    )
    assert not d.used_llm and "vaynhanh" not in d.text and OFFICIAL_HOTLINE_VAR in d.text
    assert d.sanitized_removed
