"""EC-AI21: Strict Outbound Sanitizer cho AI Copilot — tước 100% URL/SĐT lạ."""

from __future__ import annotations

import re

import pytest

from app.copilot.advisory import (
    OFFICIAL_HOTLINE_VAR,
    AdvisoryContext,
    AdvisoryWriter,
    is_outbound_safe,
    sanitize_outbound,
    suggest_shield_trigger,
)
from app.core.schemas.ops import ScamTrap, SeverityLevel

HOSTILE = [
    "Vào https://evil.example/pay để nhận tiền",
    "Truy cập www.evil-vay.vn ngay",
    "Liên hệ zalo.me/0909123456 gấp",
    "Xem bit.ly/abc123 nhé",
    "Nhấp hxxps://evil[.]com/login",
    "Mở evil dot com để xác minh",
    "Gọi 0909123456 để giải ngân",
    "Gọi 0909.123.456 để giải ngân",
    "Gọi +84 909 123 456 để giải ngân",
    "Gọi 84-909-123-456 để giải ngân",
    "Gọi 1900 6868 ngay",
    "Gọi 1800.1234 ngay",
    "Gọi ０９０９１２３４５６７ ngay",  # chữ số full-width (NFKC)
    "Gọi 0909​123456 ngay",  # ký tự rộng 0 chen giữa
    "Gọi không chín không chín một hai ba bốn năm sáu",
    "Email kẻ gian: scammer@evil.example",
    "Thanh toán tại {{EVIL_LINK}} ngay",
]


def _has_forbidden(text: str) -> bool:
    return bool(
        re.search(r"https?://|www\.|@|\d{7,}", text)
        or re.search(r"\b[a-z0-9-]+\.(?:com|vn|me|ly|example)\b", text, re.I)
        or "{{EVIL" in text
    )


@pytest.mark.parametrize("hostile", HOSTILE)
def test_ec_ai21_sanitizer_strips_urls_phones_emails_templates(hostile: str) -> None:
    """EC-AI21: không URL/SĐT/email/biến mẫu lạ nào sống sót."""
    result = sanitize_outbound(hostile)
    assert not _has_forbidden(result.text), result.text
    digits = re.sub(r"\D", "", result.text)
    assert len(digits) < 7
    assert result.removed  # có ghi nhận đã tước


def test_ec_ai21_whitelisted_hotline_becomes_template_variable_not_raw_number() -> None:
    """EC-AI21: số trong whitelist chỉ xuất hiện dưới dạng {{OFFICIAL_HOTLINE}}."""
    out = sanitize_outbound("Gọi 1900 633 633 hoặc 0909123456", whitelist_hotlines=["1900 633 633"])
    assert OFFICIAL_HOTLINE_VAR in out.text
    assert "1900" not in out.text and "0909" not in out.text


def test_ec_ai21_official_variable_survives_other_templates_removed() -> None:
    out = sanitize_outbound(f"Gọi {OFFICIAL_HOTLINE_VAR} hoặc {{{{PHISH}}}}")
    assert OFFICIAL_HOTLINE_VAR in out.text
    assert "PHISH" not in out.text


def test_ec_ai21_legit_money_amounts_and_dates_not_mangled() -> None:
    text = "Khoản phí 2.000.000đ và 10.000.000 đồng, ngày 25/11/2026, lãi 36%/năm."
    assert sanitize_outbound(text).text == text


def test_ec_ai21_is_outbound_safe() -> None:
    assert is_outbound_safe(f"Gọi {OFFICIAL_HOTLINE_VAR} nếu cần.")
    assert not is_outbound_safe("Vào http://evil.example")
    assert not is_outbound_safe("Gọi 0909123456")


class _HostileLLM:
    async def draft(self, context: AdvisoryContext) -> str:
        return (
            "Cảnh báo: chiến dịch mạo danh đang hoạt động, hãy cẩn trọng khi nhận cuộc gọi lạ. "
            "Gọi ngay 0909123456 hoặc vào https://evil.example/pay để được hỗ trợ."
        )


class _UrlOnlyLLM:
    async def draft(self, context: AdvisoryContext) -> str:
        return "https://evil.example 0909123456"


class _BrokenLLM:
    async def draft(self, context: AdvisoryContext) -> str:
        raise RuntimeError("LLM down")


CTX = AdvisoryContext(
    trap=ScamTrap.TRAP_1_ADVANCE_FEE,
    severity=SeverityLevel.CRITICAL_WAVE,
    lender_name="Home Credit",
    incident_count_24h=4,
)


async def test_ec_ai21_llm_draft_copied_from_attacker_content_is_sanitized() -> None:
    """EC-AI21: LLM (bị đầu độc) chép URL/SĐT lạ -> bản nháp vẫn sạch."""
    draft = await AdvisoryWriter(_HostileLLM()).draft(CTX)
    assert draft.used_llm
    assert not _has_forbidden(draft.text)
    assert "evil" not in draft.text
    assert set(draft.sanitized_removed) >= {"url", "phone"}
    assert draft.requires_human_approval


async def test_ec_ai21_llm_output_gutted_by_sanitizer_falls_back_to_template() -> None:
    draft = await AdvisoryWriter(_UrlOnlyLLM()).draft(CTX)
    assert not draft.used_llm
    assert OFFICIAL_HOTLINE_VAR in draft.text
    assert not _has_forbidden(draft.text)


async def test_advisory_template_when_llm_fails_or_absent() -> None:
    for writer in (AdvisoryWriter(), AdvisoryWriter(_BrokenLLM())):
        draft = await writer.draft(CTX)
        assert not draft.used_llm
        assert "Home Credit" in draft.text
        assert OFFICIAL_HOTLINE_VAR in draft.text
        assert not _has_forbidden(draft.text)
        assert "OTP" in draft.text  # nhắc không cung cấp OTP, không chứa OTP thật


@pytest.mark.parametrize("trap", list(ScamTrap))
async def test_advisory_template_covers_every_trap_and_stays_clean(trap: ScamTrap) -> None:
    draft = await AdvisoryWriter().draft(
        AdvisoryContext(trap=trap, severity=SeverityLevel.WATCH, lender_name=None)
    )
    assert is_outbound_safe(draft.text)


async def test_ec_ai21_malicious_lender_name_cannot_inject_link() -> None:
    ctx = AdvisoryContext(
        trap=ScamTrap.TRAP_3_LOAN_RECALL,
        severity=SeverityLevel.ELEVATED,
        lender_name="Evil https://evil.example 0909123456",
    )
    draft = await AdvisoryWriter().draft(ctx)
    assert is_outbound_safe(draft.text)


def test_shield_trigger_suggestion_requires_human_approval() -> None:
    assert suggest_shield_trigger(SeverityLevel.WATCH, 1) is None
    s = suggest_shield_trigger(SeverityLevel.CRITICAL_WAVE, 5)
    assert s is not None and s.requires_human_approval and s.suggested_shield_hours == 24
