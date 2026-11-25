"""Mã phán quyết dùng chung (File 2 §4.4, §4.1)."""

from __future__ import annotations

from app.core.verdict_codes import (
    APR_HIDDEN_VERDICTS,
    VERDICT_LABEL_VI,
    VERDICT_SEVERITY,
    KernelSignal,
    Severity,
    VerdictCode,
    is_red,
)


def test_nine_verdict_codes_exact() -> None:
    assert [c.value for c in VerdictCode] == [
        "V_RED_ADVANCE_FEE",
        "V_RED_BRAND_SPOOF",
        "V_RED_BNPL_OR_STRAW_SCAM",
        "V_RED_CRIMINAL_USURY",
        "V_ORANGE_CIVIL_CAP_EXCEEDED",
        "V_ORANGE_PAWN_HIGH_FEE",
        "V_YELLOW_HIGH_COST_LICENSED",
        "V_AMBER_INCOMPLETE_DOC",
        "V_GREEN_TRANSPARENT",
    ]


def test_kernel_signal_advance_fee() -> None:
    assert KernelSignal.INFINITE_APR_ADVANCE_FEE_SCAM.value == "INFINITE_APR_ADVANCE_FEE_SCAM"


def test_every_code_has_severity_and_label() -> None:
    assert set(VERDICT_SEVERITY) == set(VerdictCode)
    assert set(VERDICT_LABEL_VI) == set(VerdictCode)


def test_red_family() -> None:
    reds = {c for c in VerdictCode if is_red(c)}
    assert reds == {c for c in VerdictCode if c.value.startswith("V_RED_")}
    assert VERDICT_SEVERITY[VerdictCode.V_GREEN_TRANSPARENT] is Severity.GREEN
    assert VerdictCode.V_RED_ADVANCE_FEE in APR_HIDDEN_VERDICTS
