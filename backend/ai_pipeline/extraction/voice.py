"""``Voice-to-Cashflow Dialect Transducer`` (File 3 §1.3, EC-AI11, EC-AI12).

Biến bản ghi lời nói (đã STT) thành ``MultiImageLoanExtraction`` với
``screen_categories=[VOICE_DICTATED_TERMS]``, kèm Dải Xác nhận 3 Con Số (vay - thực nhận -
trả bao nhiêu sau mấy ngày) mà người dùng bắt buộc xác nhận trước khi chốt phán quyết.
Tất định, không gọi LLM.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .currency_slang import MoneyMention, normalize_text, normalize_vn_currency_slang
from .schemas import (
    DeductedFeeItem,
    MultiImageLoanExtraction,
    RepaymentRegimeSegment,
    ScreenCategory,
    UpfrontFeeDemand,
)

VOICE_CONFIDENCE = 0.6  # giả định: lời nói qua STT luôn < 0.82 → bắt buộc xác nhận tay

# --- EC-AI12: nhầm âm gần giống -------------------------------------------------

_CANCEL_CONTEXT = ("không vay nữa", "không muốn vay", "hoàn tiền", "trả lại tiền", "muốn bỏ")


@dataclass(frozen=True, slots=True)
class PhoneticCorrection:
    heard: str
    canonical: str
    certain: bool  # False → chỉ là khả năng, cần hỏi lại người dùng


_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:quỷ|gửi)\s+(khoản vay|hợp đồng vay)\b"), r"hủy \1"),
    (re.compile(r"\b(phí|tiền)\s+đối chứng\b"), r"\1 đối ứng"),
    (re.compile(r"\b(tiền|bị|đang)\s+trèo\b"), r"\1 treo"),
)
_AMBIGUOUS_SIGN = re.compile(r"\bký\s+(khoản vay|hợp đồng)\b")


def resolve_phonetic_confusions(text: str) -> tuple[str, tuple[PhoneticCorrection, ...]]:
    """Sửa cụm STT nghe nhầm dựa trên ngữ cảnh cả câu, không dựa từ đơn lẻ."""
    out = normalize_text(text)
    fixes: list[PhoneticCorrection] = []
    for pattern, repl in _RULES:
        for m in pattern.finditer(out):
            fixes.append(PhoneticCorrection(m.group(0), m.expand(repl), certain=True))
        out = pattern.sub(repl, out)
    if any(c in out for c in _CANCEL_CONTEXT):
        for m in _AMBIGUOUS_SIGN.finditer(out):
            fixes.append(PhoneticCorrection(m.group(0), f"hủy {m.group(1)}", certain=False))
    return out, tuple(fixes)


# --- Gán vai trò cho từng cụm tiền --------------------------------------------

_NET_KW = ("thực nhận", "nhận về", "cầm về", "về tay", "nhận được", "nhận")
_TOTAL_KW = ("tổng cộng", "tổng", "tất cả", "cả gốc lẫn lãi", "phải trả")
_PRINCIPAL_KW = ("khoản vay", "hạn mức", "vay", "mượn", "gốc")
_FEE_KW = ("phí", "bảo hiểm", "đối ứng", "mở băng", "giải tỏa", "thuế")
_UPFRONT_KW = (
    "nộp trước",
    "chuyển trước",
    "đóng trước",
    "trước khi giải ngân",
    "trước khi nhận",
    "chuyển khoản",
    "bắt nộp",
    "bắt chuyển",
)


@dataclass(frozen=True, slots=True)
class ConfirmationStrip:
    """Dải Xác nhận 3 Con Số (File 3 §1.3)."""

    principal_vnd: int | None
    net_disbursed_vnd: int | None
    repayment_total_vnd: int | None
    tenure_days: int | None


@dataclass(frozen=True, slots=True)
class VoiceTermsDraft:
    transcript_normalized: str
    slang_tokens: tuple[MoneyMention, ...]
    extraction: MultiImageLoanExtraction
    confirmation: ConfirmationStrip
    phonetic_corrections: tuple[PhoneticCorrection, ...]
    needs_confirmation: bool = True


def _last_kw(window: str, keywords: tuple[str, ...]) -> int:
    return max((window.rfind(k) for k in keywords), default=-1)


def transcribe_terms_to_extraction(transcript: str) -> VoiceTermsDraft:
    text, fixes = resolve_phonetic_confusions(transcript)
    slang = normalize_vn_currency_slang(text)
    src = slang.source_text

    principal: int | None = None
    net: int | None = None
    total: int | None = None
    tenure: int | None = slang.durations[0].days if slang.durations else None
    per_period: list[tuple[int, int]] = []  # (amount, interval_days)
    fees: list[DeductedFeeItem] = []
    upfront: UpfrontFeeDemand | None = None
    notes: list[str] = []

    prev_end = 0
    for m in slang.money:
        clause_start = max(src.rfind(sep, 0, m.start) for sep in (",", ";", ".", "\n")) + 1
        window = src[max(prev_end, clause_start) : m.start]
        clause = src[clause_start : src.find(",", m.end) if "," in src[m.end :] else len(src)]
        prev_end = m.end
        pos = {
            "fee": _last_kw(window, _FEE_KW),
            "net": _last_kw(window, _NET_KW),
            "total": _last_kw(window, _TOTAL_KW),
            "principal": _last_kw(window, _PRINCIPAL_KW),
        }
        role = max(pos, key=lambda r: pos[r]) if max(pos.values()) >= 0 else None
        if m.per_period_days is not None and (m.per_period_explicit or role is None):
            per_period.append((m.value_vnd, m.per_period_days))
            continue
        if m.per_period_days is not None and tenure is None:
            tenure = m.per_period_days  # "vay 10 củ một tháng" → thời hạn 1 tháng
        if role == "fee":
            label = window.strip() or "phí"
            if any(k in clause for k in _UPFRONT_KW):
                upfront = UpfrontFeeDemand(
                    demanded=True, fee_name_claimed=label, amount_vnd=m.value_vnd
                )
            else:
                fees.append(DeductedFeeItem(fee_label=label, amount_vnd=m.value_vnd))
        elif role == "net" and net is None:
            net = m.value_vnd
        elif role == "total" and total is None:
            total = m.value_vnd
        elif principal is None and role in ("principal", None):
            principal = m.value_vnd
        else:
            notes.append(f"Chưa rõ vai trò của số tiền {m.value_vnd:,} đồng".replace(",", "."))

    regimes: list[RepaymentRegimeSegment] = []
    for amount, interval in per_period:
        if tenure is None:
            notes.append("Chưa nghe rõ góp trong bao lâu — vui lòng nói thêm thời hạn.")
            continue
        count = max(1, math.ceil(tenure / interval))
        regimes.append(
            RepaymentRegimeSegment(
                period_count=min(count, 360),
                period_interval_days=interval,
                amount_per_period_vnd=amount,
            )
        )
    for fix in fixes:
        if not fix.certain:
            notes.append(f'Nghe được "{fix.heard}" — có phải ý bạn là "{fix.canonical}"?')

    extraction = MultiImageLoanExtraction(
        screen_categories=[ScreenCategory.VOICE_DICTATED_TERMS],
        nominal_principal_vnd=principal or None,
        net_disbursed_vnd=net,
        total_repayment_stated_vnd=total or None,
        tenure_days_total=tenure,
        deducted_fees_at_source=fees,
        repayment_regimes=regimes,
        upfront_fee_demand=upfront or UpfrontFeeDemand(demanded=False),
        missing_critical_page_suspected=tenure is None and not regimes,
        critical_fields_min_confidence=VOICE_CONFIDENCE,
        extraction_notes_vi=notes,
    )
    schedule_total = (
        sum(r.period_count * r.amount_per_period_vnd for r in regimes) if regimes else None
    )
    confirmation = ConfirmationStrip(
        principal_vnd=principal,
        net_disbursed_vnd=net,
        repayment_total_vnd=total if total is not None else schedule_total,
        tenure_days=tenure,
    )
    return VoiceTermsDraft(
        transcript_normalized=slang.normalized_text,
        slang_tokens=slang.money,
        extraction=extraction,
        confirmation=confirmation,
        phonetic_corrections=fixes,
    )
