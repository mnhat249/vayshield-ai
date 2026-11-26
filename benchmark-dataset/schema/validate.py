"""Validate VayShield-Bench-340 labels: JSON Schema + cross-field/ground-truth consistency.

Usage: uv run --project backend python benchmark-dataset/schema/validate.py
Exit code 0 = OK, 1 = errors.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

bc = importlib.import_module("bench_common")

try:
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover
    import subprocess

    # Re-run inside the backend project environment (has jsonschema in the dev group).
    backend = HERE.parents[1] / "backend"
    sys.exit(
        subprocess.call(
            [
                "uv",
                "run",
                "--project",
                str(backend),
                "python",
                str(Path(__file__).resolve()),
            ]
        )
    )

ROOT = HERE.parent
DATA = ROOT / "data"
LABELS = ROOT / "labels"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema_errors(name: str, samples: list[dict[str, Any]]) -> list[str]:
    validator = Draft202012Validator(_load(HERE / f"{name}.schema.json"))
    errs: list[str] = []
    for s in samples:
        for e in validator.iter_errors(s):
            loc = "/".join(str(p) for p in e.path)
            errs.append(f"{name} {s.get('id', '?')}: {loc}: {e.message}")
    return errs


def _check_contract(samples: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    codes = bc.spec_verdict_codes()
    groups = Counter(s["group"] for s in samples)
    for g, n in groups.items():
        if n != 10:
            errs.append(f"contract group {g} has {n} != 10")
    for s in samples:
        sid = s["id"]
        for img in s["images"]:
            if not (DATA / img["file"]).is_file():
                errs.append(f"{sid}: missing image {img['file']}")
        flows = [(c["day"], c["amount_vnd"]) for c in s["cashflows"]]
        f = s["flags"]
        if s["verdict"] not in codes:
            errs.append(f"{sid}: verdict {s['verdict']} not one of the 9 spec codes")
        if f["advance_fee"]:
            # F_upfront > 0 and P_disbursed == 0: kernel stops, APR/EAR are null (File 2 s4.1)
            if (
                s["expected_apr_vn_365"] is not None
                or s["expected_ear_xirr"] is not None
            ):
                errs.append(f"{sid}: advance-fee sample must have null APR/EAR")
        else:
            apr = bc.apr_vn_365_percent(flows)
            if s["expected_apr_vn_365"] is None or (
                abs(float(apr) - s["expected_apr_vn_365"]) > 0.005
            ):
                errs.append(
                    f"{sid}: expected_apr_vn_365 {s['expected_apr_vn_365']} != recomputed {apr}"
                )
            ear = bc.ear_xirr_percent_or_none(flows)
            want_ear = None if ear is None else float(ear)
            got_ear = s["expected_ear_xirr"]
            if (want_ear is None) != (got_ear is None) or (
                want_ear is not None and abs(want_ear - got_ear) > 0.005
            ):
                errs.append(
                    f"{sid}: expected_ear_xirr {got_ear} != recomputed {want_ear}"
                )
        v = bc.verdict_for(
            s["expected_apr_vn_365"],
            advance_fee=f["advance_fee"],
            spoof=f["spoof"],
            bnpl_or_straw=f["bnpl_cashout"] or f["straw_borrower"],
            licensed=f["licensed_entity"],
            p2p_over_cap=f["p2p_over_cap"],
            pawn=f["pawn_contract"],
            incomplete_doc=f["incomplete_doc"],
        )
        if v != s["verdict"]:
            errs.append(f"{sid}: verdict {s['verdict']} != rule {v}")
        if f["injection"] != bool(s["injection_text"]):
            errs.append(f"{sid}: injection flag/text mismatch")
        if f["p2p_over_cap"] and s["principal_vnd"] <= 100_000_000:
            errs.append(f"{sid}: p2p_over_cap but principal <= 100tr")
    return errs


def _check_voice(samples: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    regions = Counter(s["region"] for s in samples)
    if len(regions) != 3:
        errs.append("voice: need 3 regions")
    if sum(1 for s in samples if s["noise_db"] == 80) < 10:
        errs.append("voice: need >=10 samples with 80 dB noise")
    for s in samples:
        if s["audio_file"] is None and not s["audio_pending"]:
            errs.append(f"{s['id']}: no audio and audio_pending=false")
        if s["audio_file"] and not (DATA / s["audio_file"]).is_file():
            errs.append(f"{s['id']}: missing audio file")
        for t in s["slang_terms"]:
            if t["surface"] not in s["transcript"]:
                errs.append(f"{s['id']}: slang '{t['surface']}' not in transcript")
    return errs


def _check_coercion(samples: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    for s in samples:
        scam = s["label"] == "SCAM"
        if scam and not s["axes"]:
            errs.append(f"{s['id']}: SCAM without axes")
        if not scam and (s["axes"] or s["coached"]):
            errs.append(f"{s['id']}: ROUTINE must have no axes/coached")
        if (s["expected_action"] == "RED_ESCALATION") != scam:
            errs.append(f"{s['id']}: expected_action inconsistent with label")
        if s["coached"] and "C7" not in s["axes"]:
            errs.append(f"{s['id']}: coached requires axis C7")
    if sum(1 for s in samples if s["label"] == "ROUTINE") != 30:
        errs.append("coercion: need exactly 30 ROUTINE")
    for k in range(1, 11):
        if sum(1 for s in samples if f"C{k}" in s["axes"]) < 9:
            errs.append(f"coercion: axis C{k} has < 9 samples")
    return errs


def _check_notif(samples: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    want = {"DISBURSEMENT": 35, "SALARY_P2P": 25, "DEBIT_REFUND": 20, "OTP": 20}
    got = Counter(s["category"] for s in samples)
    if dict(got) != want:
        errs.append(f"notif categories {dict(got)} != {want}")
    if len({s["bank"] for s in samples}) != 15:
        errs.append("notif: need 15 distinct banks")
    for s in samples:
        sid = s["id"]
        if s["is_disbursement"] != (s["category"] == "DISBURSEMENT"):
            errs.append(f"{sid}: is_disbursement mismatch")
        if s["contains_otp"] != (s["category"] == "OTP") or s["contains_otp"] != bool(
            s["otp_value"]
        ):
            errs.append(f"{sid}: OTP label mismatch")
        for span in s["pii_spans"]:
            if span not in s["text"]:
                errs.append(f"{sid}: pii span not in text")
        if s["otp_value"] and s["otp_value"] not in s["text"]:
            errs.append(f"{sid}: otp not in text")
        if s["expected_masked"] != bc.mask_text(
            s["text"], s["pii_spans"], s["otp_value"]
        ):
            errs.append(f"{sid}: expected_masked != reference mask")
        if bc.LONG_DIGITS.search(s["expected_masked"]):
            errs.append(f"{sid}: masked text still has >=6-digit run")
    return errs


CHECKS = {
    "contract": _check_contract,
    "voice": _check_voice,
    "coercion": _check_coercion,
    "notif": _check_notif,
}


def validate_all() -> list[str]:
    errs: list[str] = []
    for name, size in bc.SET_SIZES.items():
        path = LABELS / f"{name}.json"
        if not path.is_file():
            errs.append(f"missing {path}")
            continue
        samples = _load(path)
        if len(samples) != size:
            errs.append(f"{name}: {len(samples)} samples != {size}")
        ids = [s.get("id") for s in samples]
        if len(set(ids)) != len(ids):
            errs.append(f"{name}: duplicate ids")
        se = _schema_errors(name, samples)
        errs.extend(se)
        if not se:
            errs.extend(CHECKS[name](samples))
    return errs


def main() -> int:
    errs = validate_all()
    if errs:
        for e in errs:
            print("ERROR", e)
        print(f"FAIL: {len(errs)} error(s)")
        return 1
    print("OK: VayShield-Bench-340 labels valid (80 + 40 + 120 + 100 = 340)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
