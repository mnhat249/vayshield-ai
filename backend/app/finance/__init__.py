"""Kernel tài chính tất định (File 2 §4.1–§4.3). Hàm thuần, không I/O.

Lãi suất trả về là phân số/năm (5.01 = 501%/năm); tiền là int đồng.
"""

from app.finance.cashflow import (
    CashFlowPoint,
    CommodityResale,
    LoanTerms,
    RepaymentRegime,
    build_cashflows,
    bullet_cashflows,
    count_sign_changes,
    days_from_dates,
    pmt,
)
from app.finance.errors import (
    AdvanceFeeScamError,
    FinanceError,
    MultipleRootsError,
    RateOutOfBracketError,
)
from app.finance.livelihood import (
    LivelihoodPain,
    RolloverMode,
    RolloverSpiral,
    daily_wage_from_monthly_income,
    livelihood_pain,
    rollover_spiral,
)
from app.finance.solvers import (
    LegalCapPosition,
    RateReport,
    analyze_cashflows,
    compare_to_legal_cap,
    interval_crosses,
    solve_apr_interval_bounds,
    solve_ear_xirr,
    solve_mirr,
    solve_vn_legal_apr_365,
    stated_rate_contradiction,
)

__all__ = [
    "AdvanceFeeScamError",
    "CashFlowPoint",
    "CommodityResale",
    "FinanceError",
    "LegalCapPosition",
    "LivelihoodPain",
    "LoanTerms",
    "MultipleRootsError",
    "RateOutOfBracketError",
    "RateReport",
    "RepaymentRegime",
    "RolloverMode",
    "RolloverSpiral",
    "analyze_cashflows",
    "build_cashflows",
    "bullet_cashflows",
    "compare_to_legal_cap",
    "count_sign_changes",
    "daily_wage_from_monthly_income",
    "days_from_dates",
    "interval_crosses",
    "livelihood_pain",
    "pmt",
    "rollover_spiral",
    "solve_apr_interval_bounds",
    "solve_ear_xirr",
    "solve_mirr",
    "solve_vn_legal_apr_365",
    "stated_rate_contradiction",
]
