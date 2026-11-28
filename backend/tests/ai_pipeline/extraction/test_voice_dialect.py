"""T19 — trợ từ cuối câu vùng miền Trung/Nam (EC-AI11): dựng được lịch trả, KHÔNG BAO GIỜ số sai."""

from __future__ import annotations

import random

import pytest

from ai_pipeline.extraction.currency_slang import normalize_vn_currency_slang
from ai_pipeline.extraction.voice import transcribe_terms_to_extraction

_TAILS = ["nghe", "rứa", "nghen", "nhen", "hén", "nè", "hông", "đa", "nha", "hen", "đó", "đấy"]
_BASE = "Tui mượn sáu củ, mỗi tháng đóng ba chai, đóng bốn tháng"


def _regime(text: str) -> tuple[int | None, int | None, int | None, int | None]:
    e = transcribe_terms_to_extraction(text).extraction
    r = e.repayment_regimes[0] if e.repayment_regimes else None
    return (
        e.nominal_principal_vnd,
        r.amount_per_period_vnd if r else None,
        r.period_interval_days if r else None,
        r.period_count if r else None,
    )


@pytest.mark.parametrize("tail", _TAILS)
def test_ec_ai11_dialect_tail_builds_regime(tail: str) -> None:
    assert _regime(f"{_BASE} {tail}.") == (6_000_000, 3_000_000, 30, 4)
    assert _regime(f"{_BASE} {tail}") == (6_000_000, 3_000_000, 30, 4)


@pytest.mark.parametrize("tail", ["nghe nha", "rứa nghe", "nè nha", "nghen nhé"])
def test_ec_ai11_dialect_stacked_tails(tail: str) -> None:
    assert _regime(f"{_BASE} {tail}") == (6_000_000, 3_000_000, 30, 4)


def test_ec_ai11_dialect_tail_after_each_period_kind() -> None:
    assert _regime("Em vay sáu củ rưỡi, mỗi tuần đóng 900 k, đóng mười hai tuần nghe")[1:] == (
        900_000,
        7,
        12,
    )
    assert _regime("Tui mượn mười bảy chai, mỗi ngày đóng tám xị, đóng sáu ngày rứa")[1:] == (
        800_000,
        1,
        6,
    )


def test_ec_ai11_dialect_tail_money_boundary() -> None:
    # đuôi vùng miền là ranh giới sau số: "2 triệu 5 nghe" = 2,5 triệu
    m = normalize_vn_currency_slang("vay 2 triệu 5 nghe").money
    assert [x.value_vnd for x in m] == [2_500_000]


@pytest.mark.parametrize(
    "text",
    [
        # "rồi" = đã đóng 4 tháng (quá khứ), KHÔNG phải thời hạn
        f"{_BASE} rồi",
        # trợ từ giữa câu không phải ranh giới
        "Tui mượn sáu củ, mỗi tháng đóng ba chai, đóng bốn tháng nghe nói là lãi cao",
        "Tui mượn sáu củ, mỗi tháng đóng ba chai, đóng bốn tháng hông nổi",
        # chỉ định, không đứng sau động từ trả/vay
        "Tui mượn sáu củ, mỗi tháng đóng ba chai, bốn tháng đó",
        "Tui mượn sáu củ, mỗi tháng đóng ba chai, đóng bốn hôm đó",
        "Tui mượn sáu củ, mỗi tháng đóng ba chai, đóng bốn tháng đó rồi",
    ],
)
def test_ec_ai11_dialect_non_tail_never_builds_wrong_regime(text: str) -> None:
    """Đúng hoặc None: nếu có lịch thì chỉ được là lịch đúng, không số khác."""
    got = _regime(text)
    assert got[1:] in {(None, None, None), (3_000_000, 30, 4)} or got[3] is None
    assert got[3] in (None, 4)


def test_ec_ai11_dialect_rồi_tail_stays_none() -> None:
    assert _regime(f"{_BASE} rồi")[1:] == (None, None, None)


_NUM = {2: "hai", 3: "ba", 4: "bốn", 5: "năm", 6: "sáu", 8: "tám", 10: "mười", 12: "mười hai"}
_UNIT = {"ngày": 1, "tuần": 7, "tháng": 30}
_AMT = {"ba chai": 3_000_000, "hai củ rưỡi": 2_500_000, "900 k": 900_000, "tám xị": 800_000}
_BAD_TAILS = ["rồi", "qua", "mai", "nay", "ngoái", "nữa", "luôn rồi", "ạ rồi", "nói"]


@pytest.mark.parametrize("seed", range(12))
def test_ec_ai11_dialect_fuzz_never_wrong(seed: int) -> None:
    """Fuzz: đuôi tốt → lịch đúng; đuôi xấu/giữa câu → đúng hoặc None, không bao giờ số khác."""
    rnd = random.Random(seed)
    for _ in range(150):
        n = rnd.choice(list(_NUM))
        unit = rnd.choice(list(_UNIT))
        amt_s, amt = rnd.choice(list(_AMT.items()))
        sub = rnd.choice(["Tui mượn", "Em vay", "Con vay", "Mình vay"])
        head = f"{sub} sáu chai, mỗi {unit} đóng {amt_s}, đóng {_NUM[n]} {unit}"
        want = (6_000_000, amt, _UNIT[unit], n)
        good = rnd.choice(_TAILS)
        assert _regime(f"{head} {good}") == want, f"{head} {good}"
        bad = rnd.choice(_BAD_TAILS)
        got = _regime(f"{head} {bad}")
        assert got == want or got[3] is None, f"{head} {bad} -> {got}"
        mid = rnd.choice(_TAILS)
        got = _regime(f"{head} {mid} {rnd.choice(['thì', 'mà', 'vì'])} lãi cao lắm")
        assert got == want or got[3] is None, f"{head} {mid} ... -> {got}"
