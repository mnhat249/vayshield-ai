"""Xuất ``summary.md`` + CSV cho Nhóm 2 (slide 8). Không có dấu thời gian để kết quả tái lập."""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from bench_core import Metric, fmt_value
from suites import ContractResult

FAKE_DISCLAIMER = (
    "> **CANH BAO TRUNG THUC (che do fake-offline):** buoc doc anh cua Gemini duoc thay bang "
    "oracle dung tu chinh nhan chuan, va baseline 'Pure LLM' o Ablation 1 la mo phong tat dinh. "
    "Ket qua nay CHI KIEM HA TANG + bo giai tai chinh/phan loai tat dinh, KHONG phai do chinh xac "
    "cua Gemini that va KHONG duoc dua len slide nhu bang chung do AI. Muon do that: dat "
    "`BENCH_LIVE=1` + `BENCH_GEMINI_API_KEY` (ton chi phi, gui du lieu tong hop ra ngoai; can nguoi duyet)."
)


def write_metrics_csv(path: Path, metrics: Sequence[Metric], mode: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["mode", "dataset", "metric", "value", "unit", "n", "sla", "status", "note"])
        for m in metrics:
            w.writerow(
                [
                    mode,
                    m.dataset,
                    m.name,
                    "" if m.value is None else repr(m.value),
                    m.unit,
                    m.n,
                    m.sla.text() if m.sla else "",
                    m.status,
                    m.note,
                ]
            )


def write_contract_csv(path: Path, rows: Sequence[ContractResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "id",
                "group",
                "expected_apr_pct",
                "apr_pct",
                "abs_err_pp",
                "advance_fee_label",
                "advance_fee_detected",
                "injection_label",
                "injection_flagged",
                "injection_apr_unchanged",
                "qr_memo_flagged",
            ]
        )
        for r in rows:
            w.writerow(
                [
                    r.sample_id,
                    r.group,
                    "" if r.expected_apr is None else r.expected_apr,
                    "" if r.apr_pct is None else f"{r.apr_pct:.4f}",
                    "" if r.abs_err_pct is None else f"{r.abs_err_pct:.4f}",
                    int(r.advance_fee_label),
                    int(r.advance_fee_detected),
                    int(r.injection_label),
                    int(r.injection_flagged),
                    int(r.injection_apr_unchanged),
                    "" if r.qr_memo_flagged is None else int(r.qr_memo_flagged),
                ]
            )


def _table(metrics: Sequence[Metric]) -> list[str]:
    lines = [
        "| Tap/Thi nghiem | Metric | Gia tri | n | SLA | Trang thai | Ghi chu |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for m in metrics:
        sla = m.sla.text() if m.sla else "-"
        lines.append(
            f"| {m.dataset} | {m.name} | {fmt_value(m)} | {m.n} | {sla} | {m.status} | {m.note} |"
        )
    return lines


def render_summary(
    metrics: Sequence[Metric],
    *,
    mode: str,
    quick: bool,
    sizes: dict[str, int],
    gemini_calls: int,
) -> str:
    bench = [m for m in metrics if not m.dataset.startswith("Ablation")]
    abl = [m for m in metrics if m.dataset.startswith("Ablation")]
    fails = [m for m in metrics if m.status == "FAIL"]
    nm = [m for m in metrics if m.status == "NOT_MEASURED"]
    out = [
        "# VayShield-Bench-340 - ket qua chay runner (T17)",
        "",
        f"- Che do: `{mode}`{' (--quick: mau con phan tang)' if quick else ' (day du)'}",
        "- So mau da chay: " + ", ".join(f"{k}={v}" for k, v in sizes.items()),
        f"- So lan goi Gemini GIA (oracle): {gemini_calls}; goi Gemini that: "
        + ("xem muc Ablation1-LIVE" if mode == "live-gemini" else "0"),
        "",
    ]
    if mode != "live-gemini":
        out += [FAKE_DISCLAIMER, ""]
    out += ["## Bang metric (File 3 s6.1)", "", *_table(bench), ""]
    out += ["## Ablation (File 3 s6.2)", "", *_table(abl), ""]
    out += ["## Cho CHUA dat SLA / chua do duoc", ""]
    if not fails and not nm:
        out.append("- Khong co.")
    for m in fails:
        out.append(
            f"- FAIL {m.dataset} / {m.name}: dat {fmt_value(m)} (SLA {m.sla.text() if m.sla else '-'})."
        )
    for m in nm:
        out.append(f"- NOT_MEASURED {m.dataset} / {m.name}: {m.note}")
    out += [
        "",
        "## Tai lap",
        "",
        "```bash",
        "uv run --project backend python benchmark-dataset/runner/run_all.py --quick   # nhanh",
        "uv run --project backend python benchmark-dataset/runner/run_all.py           # day du",
        "```",
        "",
        "File CSV: `metrics.csv` (moi dong 1 metric + trang thai), `contract_per_sample.csv` "
        "(APR/ca advance-fee/injection tung mau).",
        "",
    ]
    return "\n".join(out)
