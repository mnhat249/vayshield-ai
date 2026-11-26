"""Ma trận phán quyết 4 chiều 9 nhãn tất định (File 2 §4.4, §1.3 chặng 4–5).

Nguyên tắc:

- Hàm thuần, không I/O; cùng đầu vào → cùng phán quyết.
- Mỗi luật độc lập "bắn" ra 1 ứng viên; phán quyết = ứng viên đứng đầu ``RULE_PRIORITY``.
  Thứ tự ưu tiên trùng thứ tự mức nghiêm trọng (đỏ khẩn cấp → xanh), nên thêm một tín hiệu rủi
  ro chỉ có thể GIỮ hoặc NÂNG mức cảnh báo, không bao giờ hạ (LLM không thể hạ phán quyết).
- Lãi suất là phân số/năm (T02); mốc 0.20 (dung sai ±0.005, S2-EC42), 0.35, 1.00. Khi điền
  contract (%/năm) mới nhân 100 (``to_audit_verdict_computed``).
- Khoảng nhạy ``[APR_min, APR_max]``: tín dụng đen chỉ khi ``APR_min >= 1.00``; khoảng cắt ngang
  mốc pháp lý → ``V_AMBER_INCOMPLETE_DOC`` (S2-EC41).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final

from app.core.schemas.common import LegalCategory
from app.core.schemas.sse import AuditVerdictComputed
from app.core.verdict_codes import (
    APR_HIDDEN_VERDICTS,
    VERDICT_LABEL_VI,
    KernelSignal,
    Severity,
    VerdictCode,
    severity_of,
)
from app.finance.errors import FinanceError
from app.finance.livelihood import RolloverSpiral, livelihood_pain, rollover_spiral
from app.finance.solvers import (
    LegalCapPosition,
    compare_to_legal_cap,
    interval_crosses,
    stated_rate_contradiction,
)
from app.verdict.fees import has_bundled_insurance
from app.verdict.inputs import Provenance, SpecialMorphology, VerdictInput
from app.verdict.interval import AprInterval
from app.verdict.legal_basis import LEGAL_BASIS_BY_VERDICT, ND_94_2025
from app.verdict.lookalike import MatchStatus
from app.verdict.matching import LenderMatch
from app.verdict.messages import Contradiction, fmt_pct, fmt_vnd, reason_text, tts_script

__all__ = [
    "CIVIL_CAP",
    "CRIMINAL_USURY_FLOOR",
    "HIGH_COST_THRESHOLD",
    "RULE_PRIORITY",
    "VerdictObject",
    "decide_verdict",
]

CIVIL_CAP: Final = 0.20
HIGH_COST_THRESHOLD: Final = 0.35
CRIMINAL_USURY_FLOOR: Final = 1.00

# Thứ tự ưu tiên tường minh khi nhiều luật cùng khớp (= thứ tự mức nghiêm trọng).
RULE_PRIORITY: Final[tuple[VerdictCode, ...]] = (
    VerdictCode.V_RED_ADVANCE_FEE,
    VerdictCode.V_RED_BRAND_SPOOF,
    VerdictCode.V_RED_BNPL_OR_STRAW_SCAM,
    VerdictCode.V_RED_CRIMINAL_USURY,
    VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED,
    VerdictCode.V_ORANGE_PAWN_HIGH_FEE,
    VerdictCode.V_YELLOW_HIGH_COST_LICENSED,
    VerdictCode.V_AMBER_INCOMPLETE_DOC,
    VerdictCode.V_GREEN_TRANSPARENT,
)

_APR_BEARING_REASONS: Final = frozenset(
    {
        Contradiction.STATED_RATE_CONTRADICTION,
        Contradiction.LOAN_EXTENSION_FEE,
        Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD,
    }
)

_LICENSED_TCTD: Final = frozenset({LegalCategory.COMMERCIAL_BANK, LegalCategory.LICENSED_FINCO})
_NON_TCTD_REGISTERED: Final = frozenset(
    {LegalCategory.P2P_SANDBOX, LegalCategory.BNPL_WALLET_PROVIDER}
)


class LenderPath(StrEnum):
    """Nhánh pháp lý dùng để so lãi (trục 3 của ma trận)."""

    LICENSED_TCTD = "LICENSED_TCTD"  # Ngân hàng / FinCo: không áp trần 20%
    PAWN_CHAIN = "PAWN_CHAIN"  # Chuỗi cầm đồ cấp phép + có tài sản cầm cố thật
    REGISTERED_NON_TCTD = "REGISTERED_NON_TCTD"  # P2P Sandbox đúng hạn mức, ví trả sau
    UNLICENSED = "UNLICENSED"  # Cá nhân / app không phép / mạo danh / P2P vượt trần


@dataclass(frozen=True, slots=True, kw_only=True)
class VerdictObject:
    """Phán quyết tất định. Lãi suất ở đây là phân số/năm; ``to_audit_verdict_computed`` đổi
    sang %/năm cho contract. ``apr_*`` là ``None`` khi nhãn ẩn APR (phí ứng trước)."""

    verdict_code: VerdictCode
    severity: Severity
    headline_vi: str
    reasons_vi: tuple[str, ...]
    contradictions: tuple[Contradiction, ...]
    legal_basis_ids: tuple[str, ...]
    tts_script: str
    fired_rules: tuple[VerdictCode, ...]
    lender_path: LenderPath
    kernel_signal: KernelSignal | None = None
    apr_vn_365: float | None = None
    apr_min: float | None = None
    apr_max: float | None = None
    ear_xirr: float | None = None
    matched_entity_code: str | None = None
    lookalike_of: str | None = None
    official_hotline_verify: str | None = None
    c_loss_vnd: int | None = None
    lost_workdays: Decimal | None = None
    rollover: RolloverSpiral | None = None

    def to_audit_verdict_computed(self) -> AuditVerdictComputed:
        def pct(x: float | None) -> float | None:
            return None if x is None else round(x * 100, 4)

        return AuditVerdictComputed(
            verdict_code=self.verdict_code,
            severity=self.severity,
            kernel_signal=self.kernel_signal,
            apr_vn_365=pct(self.apr_vn_365),
            apr_min_bound=pct(self.apr_min),
            apr_max_bound=pct(self.apr_max),
            ear_xirr=pct(self.ear_xirr),
            lost_workdays=None if self.lost_workdays is None else float(self.lost_workdays),
            official_hotline_verify=self.official_hotline_verify,
            contradictions=[c.value for c in self.contradictions],
        )


def _lender_path(
    extraction: VerdictInput, match: LenderMatch, contradictions: list[Contradiction]
) -> LenderPath:
    record = match.record
    if match.status is not MatchStatus.EXACT or record is None:
        return LenderPath.UNLICENSED
    if extraction.special_morphology is SpecialMorphology.COMMODITY_RESALE_USURY:
        return LenderPath.UNLICENSED  # bên thu mua lại tại quầy mới là bên cho vay thực chất
    category = record.legal_category
    if category is LegalCategory.P2P_SANDBOX:
        principal = extraction.principal_vnd
        if principal is not None and principal > record.p2p_cap_vnd:
            contradictions.append(Contradiction.P2P_SANDBOX_CAP_EXCEEDED)
            return LenderPath.UNLICENSED
        return LenderPath.REGISTERED_NON_TCTD
    if category in _LICENSED_TCTD:
        return LenderPath.LICENSED_TCTD
    if category is LegalCategory.LICENSED_PAWN_CHAIN:
        # Nhánh cầm đồ do REGISTRY quyết định; cờ ``is_pawn_asset_secured`` (LLM) không mở nhánh
        # nhẹ hơn cho bên không phép, và cũng không đẩy chuỗi cầm đồ sang nhánh khác.
        return LenderPath.PAWN_CHAIN
    if category in _NON_TCTD_REGISTERED:
        return LenderPath.REGISTERED_NON_TCTD
    return LenderPath.UNLICENSED


def _financial_rule(
    path: LenderPath,
    interval: AprInterval,
    *,
    bundled_insurance: bool,
    p2p_over_cap: bool,
    channel_verified: bool,
    contradictions: list[Contradiction],
) -> VerdictCode:
    lo, hi = interval.apr_min, interval.apr_max
    if lo is None or hi is None:
        contradictions.append(Contradiction.APR_UNAVAILABLE)
        return VerdictCode.V_AMBER_INCOMPLETE_DOC

    if path is LenderPath.LICENSED_TCTD:
        if not channel_verified:
            # Quyết định của người dùng: chỉ có ảnh chụp (chưa kiểm chữ ký APK) thì tên khớp
            # ngân hàng/FinCo cũng không đủ để ra XANH/VÀNG CAM → tối đa AMBER.
            contradictions.append(Contradiction.LENDER_NOT_VERIFIED)
            return VerdictCode.V_AMBER_INCOMPLETE_DOC
        if bundled_insurance:
            contradictions.append(Contradiction.BUNDLED_INSURANCE)
        if hi > HIGH_COST_THRESHOLD or bundled_insurance:
            return VerdictCode.V_YELLOW_HIGH_COST_LICENSED
        return VerdictCode.V_GREEN_TRANSPARENT

    # Mọi chủ thể không phải ngân hàng / FinCo: kiểm mốc hình sự 100%/năm trên cận dưới.
    if lo >= CRIMINAL_USURY_FLOOR:
        return VerdictCode.V_RED_CRIMINAL_USURY
    # Trần dân sự 20%/năm kèm dung sai biên ±0,5 điểm % (S2-EC42).
    above_civil = compare_to_legal_cap(lo, CIVIL_CAP) is LegalCapPosition.ABOVE
    crosses_civil = (
        not above_civil and compare_to_legal_cap(hi, CIVIL_CAP) is LegalCapPosition.ABOVE
    )
    if crosses_civil or interval_crosses((lo, hi), CRIMINAL_USURY_FLOOR):
        contradictions.append(Contradiction.INTERVAL_CROSSES_LEGAL_THRESHOLD)

    if path is LenderPath.PAWN_CHAIN:
        if above_civil:  # cận dưới đã > 20,5%: tối thiểu CAM (khoảng cắt 100% không hạ nhãn)
            return VerdictCode.V_ORANGE_PAWN_HIGH_FEE
        return (
            VerdictCode.V_AMBER_INCOMPLETE_DOC
            if crosses_civil
            else (VerdictCode.V_GREEN_TRANSPARENT)
        )

    if above_civil:  # chắc chắn vượt trần dân sự dù cận trên có cắt mốc 100% (S2-EC41)
        return VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED
    if p2p_over_cap:  # vi phạm hạn mức Sandbox (S2-EC17) — xét trước nhánh cắt mốc 20%
        return VerdictCode.V_ORANGE_CIVIL_CAP_EXCEEDED
    if crosses_civil:
        return VerdictCode.V_AMBER_INCOMPLETE_DOC
    if path is LenderPath.REGISTERED_NON_TCTD:
        return VerdictCode.V_GREEN_TRANSPARENT
    contradictions.append(Contradiction.LENDER_NOT_VERIFIED)
    return VerdictCode.V_AMBER_INCOMPLETE_DOC


def _upfront_demanded(extraction: VerdictInput, interval: AprInterval) -> bool:
    return (
        extraction.upfront_fee_demand.demanded
        or interval.kernel_signal is KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM
    )


def _spoof_signals(
    extraction: VerdictInput,
    match: LenderMatch,
    provenance: Provenance,
    interval: AprInterval,
) -> list[Contradiction]:
    if match.status is MatchStatus.NONE or match.record is None:
        return []
    found: list[Contradiction] = []
    if match.status is MatchStatus.LOOKALIKE:
        found.append(
            Contradiction.BRAND_ENTITY_MISMATCH if match.conflict else Contradiction.LOOKALIKE_BRAND
        )
    if provenance.apk_signature_ok is False:
        found.append(Contradiction.APK_SIGNATURE_MISMATCH)
    if provenance.beneficiary_is_personal_account is True:
        found.append(Contradiction.PERSONAL_BENEFICIARY_ACCOUNT)
    if (
        provenance.account_frozen_notice
        or extraction.upfront_fee_demand.fake_frozen_receipt_claimed
    ):
        found.append(Contradiction.FAKE_FROZEN_ACCOUNT_NOTICE)
    # Mâu thuẫn thương hiệu – lãi suất: tên ngân hàng/FinCo nhưng cận dưới >= 100%/năm mà kênh
    # chưa được xác thực bằng chữ ký APK (chặng 4).
    if (
        match.status is MatchStatus.EXACT
        and match.record.legal_category in _LICENSED_TCTD
        and provenance.apk_signature_ok is not True
        and interval.apr_min is not None
        and interval.apr_min >= CRIMINAL_USURY_FLOOR
    ):
        found.append(Contradiction.LICENSED_BRAND_USURIOUS_RATE)
    return found


def _rollover(extraction: VerdictInput) -> RolloverSpiral | None:
    terms = extraction.rollover
    if terms is None:
        return None
    return rollover_spiral(
        terms.principal_vnd, terms.extension_fee_vnd, terms.extension_period_days
    )


def _livelihood(extraction: VerdictInput, advance_fee: bool) -> tuple[int | None, Decimal | None]:
    wage = extraction.daily_wage_vnd
    if advance_fee:
        amount = extraction.upfront_fee_demand.amount_vnd
        if not amount:
            return None, None
        days = (Decimal(amount) / Decimal(wage)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        return amount, days
    if not extraction.cashflows:
        return None, None
    try:
        pain = livelihood_pain(extraction.cashflows, wage)
    except FinanceError:
        return None, None
    if pain.c_loss_vnd < 0:
        return pain.c_loss_vnd, None
    return pain.c_loss_vnd, pain.lost_workdays


def decide_verdict(
    extraction: VerdictInput,
    apr_interval: AprInterval,
    lender_match: LenderMatch,
    provenance: Provenance,
) -> VerdictObject:
    """Áp ma trận 9 nhãn. Xem docstring module cho quy tắc ưu tiên."""
    contradictions: list[Contradiction] = []
    fired: set[VerdictCode] = set()
    morph = extraction.special_morphology

    # Trục 4 bổ sung: phí gia hạn giữ gốc (S2-EC15) chỉ làm APR tăng.
    spiral = _rollover(extraction)
    interval = apr_interval
    if spiral is not None:
        contradictions.append(Contradiction.LOAN_EXTENSION_FEE)
        if interval.kernel_signal is None:
            interval = interval.raised_to(spiral.period_apr)

    # Trục 1: phí trước giải ngân & biến tướng.
    advance_fee = _upfront_demanded(extraction, interval)
    if advance_fee:
        contradictions.append(Contradiction.ADVANCE_FEE_DEMANDED)
        fired.add(VerdictCode.V_RED_ADVANCE_FEE)
    if morph is SpecialMorphology.SCAM_RECOVERY_FRAUD:
        contradictions.append(Contradiction.SCAM_RECOVERY_FRAUD)
        fired.add(VerdictCode.V_RED_ADVANCE_FEE)
    if morph is SpecialMorphology.BNPL_ILLEGAL_CASHOUT:
        contradictions.append(Contradiction.BNPL_ILLEGAL_CASHOUT)
        fired.add(VerdictCode.V_RED_BNPL_OR_STRAW_SCAM)
    if morph is SpecialMorphology.STRAW_BORROWER_RECRUITMENT:
        contradictions.append(Contradiction.STRAW_BORROWER_RECRUITMENT)
        fired.add(VerdictCode.V_RED_BNPL_OR_STRAW_SCAM)
    if morph is SpecialMorphology.COMMODITY_RESALE_USURY:
        contradictions.append(Contradiction.COMMODITY_RESALE_USURY)

    # Trục 2: nguồn gốc thương hiệu.
    spoof = _spoof_signals(extraction, lender_match, provenance, interval)
    if spoof:
        contradictions.extend(spoof)
        fired.add(VerdictCode.V_RED_BRAND_SPOOF)

    # Trục 3 + 4: phân nhóm pháp nhân × khoảng APR.
    path = _lender_path(extraction, lender_match, contradictions)
    p2p_over_cap = Contradiction.P2P_SANDBOX_CAP_EXCEEDED in contradictions
    if not interval.known and interval.kernel_signal is not None:
        pass  # kernel đã dừng do phí ứng trước: không có APR để so.
    else:
        fired.add(
            _financial_rule(
                path,
                interval,
                bundled_insurance=has_bundled_insurance(extraction.deducted_fees),
                p2p_over_cap=p2p_over_cap,
                channel_verified=provenance.apk_signature_ok is True,
                contradictions=contradictions,
            )
        )

    # Cổng đủ chứng từ (chặng 5): chỉ thêm ứng viên AMBER, không gỡ luật nào khác (cờ do LLM đặt
    # không được hạ nhãn nặng hơn). GIẢ ĐỊNH với T11: ``apr_interval`` chỉ được dựng từ kỳ hạn /
    # lịch trả THỰC SỰ đọc được; nếu thiếu trang làm khuyết kỳ hạn thì T11 phải truyền
    # ``AprInterval.unavailable(...)`` (→ AMBER) hoặc khoảng phủ mọi kỳ hạn khả dĩ. Khi đó cận
    # dưới APR là đúng và V_RED_CRIMINAL_USURY (cận dưới >= 100%) được giữ (File 2 §1.3).
    if extraction.missing_critical_page_suspected:
        contradictions.append(Contradiction.MISSING_CRITICAL_PAGE)
        fired.add(VerdictCode.V_AMBER_INCOMPLETE_DOC)
    if extraction.distinct_loan_brands_count >= 2:
        contradictions.append(Contradiction.MULTIPLE_LOANS_MIXED)
        fired.add(VerdictCode.V_AMBER_INCOMPLETE_DOC)

    # S2-EC13: % quảng cáo chỉ để vạch mâu thuẫn, không bao giờ thay APR thật.
    actual = interval.apr_point if interval.apr_point is not None else interval.apr_min
    stated = extraction.stated_annual_rate
    if stated is not None and actual is not None and stated_rate_contradiction(stated, actual):
        contradictions.append(Contradiction.STATED_RATE_CONTRADICTION)

    if not fired:  # chỉ xảy ra khi kernel báo phí trước nhưng đã bị gỡ — phòng thủ
        fired.add(VerdictCode.V_AMBER_INCOMPLETE_DOC)
    code = next(c for c in RULE_PRIORITY if c in fired)
    ordered_fired = tuple(c for c in RULE_PRIORITY if c in fired)
    contradictions = list(dict.fromkeys(contradictions))

    return _build(
        code=code,
        fired=ordered_fired,
        extraction=extraction,
        interval=interval,
        match=lender_match,
        path=path,
        contradictions=tuple(contradictions),
        spiral=spiral,
        advance_fee=advance_fee,
    )


def _build(
    *,
    code: VerdictCode,
    fired: tuple[VerdictCode, ...],
    extraction: VerdictInput,
    interval: AprInterval,
    match: LenderMatch,
    path: LenderPath,
    contradictions: tuple[Contradiction, ...],
    spiral: RolloverSpiral | None,
    advance_fee: bool,
) -> VerdictObject:
    record = match.record
    brand = record.official_short_name if record is not None else None
    hotline = (
        record.official_hotlines[0] if record is not None and record.official_hotlines else None
    )
    hide_apr = code in APR_HIDDEN_VERDICTS
    lo = None if hide_apr else interval.apr_min
    hi = None if hide_apr else interval.apr_max
    point = None if hide_apr else interval.apr_point
    apr_display = point if point is not None else lo
    c_loss, lost_days = _livelihood(extraction, advance_fee and hide_apr)

    if point is not None:
        apr_text = fmt_pct(point)
    elif lo is not None and hi is not None:
        apr_text = fmt_pct(lo) if lo == hi else f"{fmt_pct(lo)} đến {fmt_pct(hi)}"
    else:
        apr_text = "chưa rõ"
    values: dict[str, str] = {
        "brand": brand or "tổ chức tín dụng",
        "apr": apr_text,
        "lo": fmt_pct(lo) if lo is not None else "chưa rõ",
        "hi": fmt_pct(hi) if hi is not None else "chưa rõ",
    }
    if extraction.stated_annual_rate is not None:
        values["stated"] = fmt_pct(extraction.stated_annual_rate)
        values["actual"] = values["apr"]
    if extraction.rollover is not None and spiral is not None:
        values["fee"] = fmt_vnd(extraction.rollover.extension_fee_vnd)
        values["days"] = str(extraction.rollover.extension_period_days)
        values["rollover_apr"] = fmt_pct(spiral.period_apr)
    if record is not None and extraction.principal_vnd is not None:
        values["principal"] = fmt_vnd(extraction.principal_vnd)
        values["cap"] = fmt_vnd(record.p2p_cap_vnd)

    reasons = [reason_text(code, **values)]
    for item in contradictions:
        if hide_apr and item in _APR_BEARING_REASONS:
            continue  # nhãn ẩn APR: không lộ con số lãi suất qua lý do
        extra = dict(values)
        if item is Contradiction.LOAN_EXTENSION_FEE and "rollover_apr" in values:
            extra["apr"] = values["rollover_apr"]
        reasons.append(reason_text(item, **extra))

    legal = list(LEGAL_BASIS_BY_VERDICT[code])
    if Contradiction.P2P_SANDBOX_CAP_EXCEEDED in contradictions or (
        record is not None and record.legal_category is LegalCategory.P2P_SANDBOX
    ):
        legal.append(ND_94_2025)

    show_brand = code in (VerdictCode.V_RED_BRAND_SPOOF, VerdictCode.V_RED_ADVANCE_FEE) or (
        match.status is MatchStatus.EXACT
    )
    return VerdictObject(
        verdict_code=code,
        severity=severity_of(code),
        headline_vi=VERDICT_LABEL_VI[code],
        reasons_vi=tuple(reasons),
        contradictions=contradictions,
        legal_basis_ids=tuple(dict.fromkeys(legal)),
        tts_script=tts_script(
            code,
            brand=brand if show_brand else None,
            hotline=hotline if show_brand else None,
            apr=values["apr"] if apr_display is not None else None,
            lo=values["lo"] if lo is not None else None,
            lost_workdays=lost_days,
        ),
        fired_rules=fired,
        lender_path=path,
        kernel_signal=(
            KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM
            if interval.kernel_signal is not None
            or (advance_fee and not extraction.net_disbursed_vnd)
            else None
        ),
        apr_vn_365=point,
        apr_min=lo,
        apr_max=hi,
        ear_xirr=None if hide_apr else interval.ear_xirr,
        matched_entity_code=record.entity_code
        if match.status is MatchStatus.EXACT and record
        else None,
        lookalike_of=brand if match.status is MatchStatus.LOOKALIKE else None,
        official_hotline_verify=hotline,
        c_loss_vnd=c_loss,
        lost_workdays=lost_days,
        rollover=None if hide_apr else spiral,
    )
