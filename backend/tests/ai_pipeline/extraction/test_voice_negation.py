# ruff: noqa: E501  -- câu lời nói dài, giữ nguyên văn
"""T19 (review) — phủ định / quá khứ / câu hỏi trong lời nói (EC-AI11, EC-AI12).

Bất biến: số bị phủ định ("không vay 5 triệu", "hông phải sáu tháng") KHÔNG BAO GIỜ được điền;
"đã đóng bốn tháng" là quá khứ (không phải thời hạn); đuôi hỏi "hông?" không dựng lịch trả.
"""

from __future__ import annotations

import pytest

from ai_pipeline.extraction.voice import transcribe_terms_to_extraction

Slots = tuple[int | None, int | None, int | None, int | None]


def _slots(text: str) -> Slots:
    e = transcribe_terms_to_extraction(text).extraction
    r = e.repayment_regimes[0] if e.repayment_regimes else None
    return (
        e.nominal_principal_vnd,
        r.amount_per_period_vnd if r else None,
        r.period_interval_days if r else None,
        r.period_count if r else None,
    )


# (transcript, số GỐC không được phép xuất hiện) — số bị phủ định không bao giờ là gốc.
_NEGATED_PRINCIPAL = [
    (
        "Tui hông vay năm triệu, tui vay ba triệu, mỗi tháng đóng một triệu, đóng ba tháng nghe",
        5_000_000,
    ),
    ("Tui không vay 5 triệu nghe", 5_000_000),
    ("Tui hông có vay năm triệu nhen", 5_000_000),
    ("Hông phải năm triệu đâu, mười triệu lận", 5_000_000),
    (
        "Tui vay mười chai, hông phải năm chai nghe, mỗi tháng đóng hai chai, đóng năm tháng",
        5_000_000,
    ),
    ("Em không vay sáu củ", 6_000_000),
    ("Em không muốn vay sáu củ", 6_000_000),
    ("Em hổng vay sáu củ nha", 6_000_000),
    ("Con chẳng vay hai chục triệu đâu", 20_000_000),
    ("Tui đâu phải vay mười triệu", 10_000_000),
    ("Mình không phải vay năm triệu mà là bảy triệu", 5_000_000),
    ("Tui hông có mượn bảy củ rưỡi nghen", 7_500_000),
    ("Em khong vay nam trieu", 5_000_000),
    ("Tui hong vay 5 trieu nghe", 5_000_000),
    ("Con khong muon vay 3 trieu", 3_000_000),
    ("Tui chang vay tam cu dau", 8_000_000),
    (
        "Tui hông vay 12 triệu, vay 8 triệu thôi, mỗi tháng đóng hai triệu, đóng bốn tháng nghe",
        12_000_000,
    ),
    (
        "Em không vay mười hai triệu mà vay chín triệu, mỗi tuần đóng 900 k, đóng chín tuần",
        12_000_000,
    ),
    (
        "Tui hổng mượn năm chai rưỡi, mượn ba chai, mỗi ngày đóng sáu lít, đóng bốn ngày nè",
        5_500_000,
    ),
    ("Tui đâu có vay bốn triệu hén", 4_000_000),
    ("Hông phải vay hai triệu rưỡi nha", 2_500_000),
    ("Tui không có vay 15 triệu nè", 15_000_000),
    (
        "Không phải 9 triệu đâu, tui vay 6 triệu, đóng một triệu mỗi tháng trong sáu tháng",
        9_000_000,
    ),
    ("Em hông vay một tỷ", 1_000_000_000),
    ("Con hổng có mượn mười lăm củ đâu", 15_000_000),
    ("Tui hông vay hai chục chai nghen", 20_000_000),
]


@pytest.mark.parametrize(("text", "banned"), _NEGATED_PRINCIPAL)
def test_ec_ai11_negated_principal_never_filled(text: str, banned: int) -> None:
    assert _slots(text)[0] != banned, text


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "Tui hông vay năm triệu, tui vay ba triệu, mỗi tháng đóng một triệu, đóng ba tháng nghe",
            (3_000_000, 1_000_000, 30, 3),
        ),
        ("Tui không vay 5 triệu nghe", (None, None, None, None)),
        ("Hông phải năm triệu đâu, mười triệu lận", (10_000_000, None, None, None)),
        (
            "Tui hông vay 12 triệu, vay 8 triệu thôi, mỗi tháng đóng hai triệu, đóng bốn tháng nghe",
            (8_000_000, 2_000_000, 30, 4),
        ),
    ],
)
def test_ec_ai11_negation_keeps_the_correct_number(text: str, want: Slots) -> None:
    assert _slots(text) == want


_BASE = "Tui mượn sáu củ, mỗi tháng đóng ba chai, "


@pytest.mark.parametrize(
    "tail",
    [
        "đóng bốn tháng hông phải sáu tháng",
        "đóng bốn tháng, hông phải sáu tháng nghe",
        "không phải sáu tháng, đóng bốn tháng nghe",
        "hông phải năm tháng, đóng bốn tháng nghe",
        "đâu phải sáu tháng, bốn tháng thôi nghen",
        "đóng bốn tháng chứ hông phải sáu tháng nè",
        "khong phai sau thang, dong bon thang",
        "đóng bốn tháng hông phải hai tháng nhen",
    ],
)
def test_ec_ai11_negated_duration_never_used(tail: str) -> None:
    got = _slots(_BASE + tail)
    assert got[3] in (None, 4), (tail, got)
    assert got[3] != 6 and got[3] != 5 and got[3] != 2


@pytest.mark.parametrize(
    "tail",
    [
        "đã đóng bốn tháng đó",
        "đã đóng bốn tháng",
        "đã đóng bốn tháng nghe",
        "đã trả bốn tháng nè",
        "đã góp bốn tháng hen",
        "mới đóng bốn tháng nghen",
        "vừa đóng bốn tháng đó",
        "đóng được bốn tháng rứa",
        "đóng xong bốn tháng nghe",
        "da dong bon thang ne",
    ],
)
def test_ec_ai11_past_paid_is_not_tenure(tail: str) -> None:
    """'đã/mới/được đóng N tháng' = đã đóng, không dựng lịch trả."""
    assert _slots(_BASE + tail)[1:] == (None, None, None), tail


@pytest.mark.parametrize(
    "tail",
    ["đóng bốn tháng hông?", "đóng bốn tháng hong?", "đóng bốn tháng hông ?"],
)
def test_ec_ai11_question_tail_not_a_schedule(tail: str) -> None:
    got = _slots(_BASE + tail)
    assert got[1:] == (None, None, None), tail
    assert got[0] == 6_000_000  # số gốc nghe rõ vẫn giữ để người dùng xác nhận


def test_ec_ai11_positive_still_builds_after_negation_rules() -> None:
    assert _slots(_BASE + "đóng bốn tháng nghe") == (6_000_000, 3_000_000, 30, 4)
    assert _slots("Em vay sáu củ rưỡi, mỗi tuần đóng 900 k, đóng mười hai tuần nghe") == (
        6_500_000,
        900_000,
        7,
        12,
    )
