"""Chạy 4 tập VayShield-Bench-340 + 3 ablation, xuất results/summary.md + CSV (T17).

    uv run --project backend python benchmark-dataset/runner/run_all.py --quick
    uv run --project backend python benchmark-dataset/runner/run_all.py

Mặc định OFFLINE (Gemini giả). Gemini thật chỉ khi BENCH_LIVE=1 + BENCH_GEMINI_API_KEY.
Không đọc .env*; không ghi khóa vào kết quả.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_core import (
    MODE_FAKE,
    MODE_LIVE,
    RESULTS_DIR,
    Metric,
    ensure_paths,
    load_labels,
    pick_quick,
)

ensure_paths()

import ablations  # noqa: E402
import live_gate  # noqa: E402
import report  # noqa: E402
import suites  # noqa: E402

QUICK_SIZES = {"contract": 24, "voice": 10, "coercion": 30, "notif": 20}


async def run(
    *, quick: bool, out_dir: Path, env: Mapping[str, str] | None = None
) -> tuple[list[Metric], str]:
    """Chạy toàn bộ và ghi file kết quả; trả (metrics, mode)."""
    decision = live_gate.decide_live(env)
    mode = MODE_LIVE if decision.enabled else MODE_FAKE
    if decision.enabled:
        print(live_gate.WARNING, file=sys.stderr)
    else:
        print(f"[bench] OFFLINE (Gemini gia). Ly do: {decision.reason}", file=sys.stderr)

    data = {k: load_labels(k) for k in ("contract", "voice", "coercion", "notif")}
    if quick:
        data["contract"] = pick_quick(data["contract"], QUICK_SIZES["contract"], "group")
        # luôn giữ đủ mẫu advance-fee/injection/dual-QR trong tập con contract
        ids = {s["id"] for s in data["contract"]}
        for s in load_labels("contract"):
            f = s["flags"]
            if (
                (f["advance_fee"] or f["injection"] or s["group"] == "DUAL_QR_CHAT")
                and len([x for x in data["contract"] if x["group"] == s["group"]]) < 3
                and s["id"] not in ids
            ):
                data["contract"].append(s)
                ids.add(s["id"])
        data["voice"] = pick_quick(data["voice"], QUICK_SIZES["voice"], "region")
        data["coercion"] = pick_quick(data["coercion"], QUICK_SIZES["coercion"], "label")
        data["notif"] = pick_quick(data["notif"], QUICK_SIZES["notif"], "category")

    metrics: list[Metric] = []
    c_metrics, c_rows, gemini_calls = await suites.run_contract(data["contract"])
    metrics += c_metrics
    metrics += suites.run_voice(data["voice"])
    metrics += suites.run_coercion(data["coercion"])
    metrics += suites.run_notif(data["notif"])

    metrics += ablations.ablation1(data["contract"], c_rows)
    if decision.enabled:
        key = (env if env is not None else __import__("os").environ)[live_gate.ENV_KEY]
        metrics += await ablations.live_ablation1(data["contract"], key, decision.max_calls)
    metrics += ablations.ablation2(data["coercion"])
    adv = [r for r in c_rows if r.advance_fee_label]
    mule = await ablations.run_mule_experiment(adv)
    metrics += ablations.ablation3(mule, bool(adv))

    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = {k: len(v) for k, v in data.items()}
    (out_dir / "summary.md").write_text(
        report.render_summary(
            metrics, mode=mode, quick=quick, sizes=sizes, gemini_calls=gemini_calls
        ),
        encoding="utf-8",
        newline="\n",
    )
    report.write_metrics_csv(out_dir / "metrics.csv", metrics, mode)
    report.write_contract_csv(out_dir / "contract_per_sample.csv", c_rows)
    return metrics, mode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="mau con phan tang, chay nhanh")
    ap.add_argument("--out", type=Path, default=None, help="thu muc ket qua")
    args = ap.parse_args(argv)
    out = args.out or (RESULTS_DIR / "quick" if args.quick else RESULTS_DIR)
    metrics, mode = asyncio.run(run(quick=args.quick, out_dir=out))
    for m in metrics:
        print(f"{m.status:12} {m.dataset:18} {m.name}")
    print(f"[bench] mode={mode} -> {out}")
    return 0  # SLA chua dat KHONG lam lenh that bai; xem summary.md


if __name__ == "__main__":
    raise SystemExit(main())
