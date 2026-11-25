"""Tests for VayShield-Bench-340 labels. Run: uv run --project backend pytest benchmark-dataset/schema -q"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

bc = importlib.import_module("bench_common")
validate = importlib.import_module("validate")

ROOT = HERE.parent


def load(name: str) -> list[dict]:
    return json.loads((ROOT / "labels" / f"{name}.json").read_text(encoding="utf-8"))


def test_total_is_340() -> None:
    assert sum(len(load(n)) for n in bc.SET_SIZES) == 340


def test_full_validation_has_no_errors() -> None:
    assert validate.validate_all() == []


def test_xirr_hand_computed() -> None:
    # 1,000,000 borrowed, 1,100,000 repaid after 365 days -> exactly 10 %
    assert bc.xirr_percent([(0, 1_000_000), (365, -1_100_000)]) == bc.Decimal("10.00")


def test_ec_ai01_dark_mode_present() -> None:
    assert any(s["flags"]["dark_mode"] for s in load("contract"))


def test_ec_ai02_dual_qr_has_victim_and_scammer() -> None:
    dual = [s for s in load("contract") if s["group"] == "DUAL_QR_CHAT"]
    assert len(dual) == 10
    for s in dual:
        assert {q["sender"] for q in s["qr_codes"]} == {"victim", "scammer"}


def test_ec_ai04_qr_memo_injection_present() -> None:
    memos = [q.get("memo", "") for s in load("contract") for q in s["qr_codes"]]
    assert any("IGNORE" in m for m in memos)


def test_ec_ai05_red_stamp_present() -> None:
    assert any(s["flags"]["red_stamp_overlap"] for s in load("contract"))


def test_ec_ai06_fake_bill_is_blocked() -> None:
    fake = [s for s in load("contract") if s["group"] == "FAKE_LOGO_BILL"]
    assert len(fake) == 10 and all(
        s["verdict"] == "BLOCK" and s["flags"]["spoof"] for s in fake
    )


def test_ec_ai11_slang_values_are_consistent() -> None:
    units = {"củ": 10**6, "chai": 10**6, "xị": 10**5, "lít": 10**5, "k": 10**3}
    for s in load("voice"):
        for t in s["slang_terms"]:
            assert t["value_vnd"] % 1000 == 0
            assert any(u in t["surface"].split() for u in units)


def test_ec_ai12_ai13_coached_samples_cover_c7() -> None:
    coached = [s for s in load("coercion") if s["coached"]]
    assert len(coached) >= 11 and all("C7" in s["axes"] for s in coached)


def test_ec_ai19_masked_text_has_no_long_digit_runs() -> None:
    for s in load("notif"):
        assert not bc.LONG_DIGITS.search(s["expected_masked"]), s["id"]


def test_ec_ai18_notif_distribution() -> None:
    n = load("notif")
    cats = [s["category"] for s in n]
    assert cats.count("DISBURSEMENT") == 35 and cats.count("SALARY_P2P") == 25
    assert cats.count("DEBIT_REFUND") == 20 and cats.count("OTP") == 20
    assert len({s["bank"] for s in n}) == 15


def test_zero_otp_leak_in_masked() -> None:
    for s in load("notif"):
        if s["otp_value"]:
            assert s["otp_value"] not in s["expected_masked"]


def test_coercion_axes_and_routine() -> None:
    c = load("coercion")
    assert sum(1 for s in c if s["label"] == "ROUTINE") == 30
    for k in range(1, 11):
        assert sum(1 for s in c if f"C{k}" in s["axes"]) >= 9
    assert all(not s["axes"] for s in c if s["label"] == "ROUTINE")


def test_synthetic_only_no_real_pii() -> None:
    blob = json.dumps(load("notif") + load("contract"), ensure_ascii=False)
    for m in re.finditer(r"\b0\d{9,11}\b", blob):
        assert m.group().startswith("0000"), m.group()
