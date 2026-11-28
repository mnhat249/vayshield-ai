"""Hạ tầng chung của runner benchmark (T17): đường dẫn, nạp nhãn, kiểu Metric/SLA.

Mặc định mọi thứ chạy OFFLINE (client Gemini giả). Không đọc .env*, không gọi mạng.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RUNNER_DIR = Path(__file__).resolve().parent
DATASET_DIR = RUNNER_DIR.parent
REPO_ROOT = DATASET_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
RESULTS_DIR = DATASET_DIR / "results"

SET_NAMES = ("contract", "voice", "coercion", "notif")

# Chế độ chạy: chỉ dùng để dán nhãn kết quả, không đổi thuật toán đo.
MODE_FAKE = "fake-offline"
MODE_LIVE = "live-gemini"


def ensure_paths() -> None:
    """Cho phép ``import app`` / ``import ai_pipeline`` / module cùng thư mục khi chạy trực tiếp."""
    for p in (str(BACKEND_DIR), str(RUNNER_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)


def load_labels(name: str) -> list[dict[str, Any]]:
    path = DATASET_DIR / "labels" / f"{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected a JSON list")
    return [dict(x) for x in data]


def pick_quick(
    samples: list[dict[str, Any]], n: int, key: str | None = None
) -> list[dict[str, Any]]:
    """Mẫu con tất định, phân tầng theo ``key`` (round-robin) để chế độ --quick vẫn phủ các nhóm."""
    if n >= len(samples):
        return list(samples)
    if key is None:
        step = len(samples) / n
        return [samples[int(i * step)] for i in range(n)]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for s in samples:
        buckets.setdefault(str(s.get(key)), []).append(s)
    out: list[dict[str, Any]] = []
    depth = 0
    while len(out) < n:
        progressed = False
        for b in buckets.values():
            if depth < len(b) and len(out) < n:
                out.append(b[depth])
                progressed = True
        if not progressed:
            break
        depth += 1
    return out


@dataclass(frozen=True, slots=True)
class Sla:
    """Mục tiêu SLA (File 3 §6.1). ``op`` in {'<', '<=', '>=', '=='}."""

    op: str
    threshold: float

    def text(self) -> str:
        return f"{self.op} {self.threshold:g}"

    def check(self, value: float) -> bool:
        if self.op == "<":
            return value < self.threshold
        if self.op == "<=":
            return value <= self.threshold
        if self.op == ">=":
            return value >= self.threshold
        if self.op == "==":
            return abs(value - self.threshold) < 1e-9
        raise ValueError(f"unknown op {self.op!r}")


@dataclass(frozen=True, slots=True)
class Metric:
    """Một dòng bảng metric. ``value=None`` nghĩa là KHÔNG ĐO ĐƯỢC (lý do ở ``note``)."""

    dataset: str
    name: str
    value: float | None
    unit: str
    n: int
    sla: Sla | None = None
    note: str = ""

    @property
    def status(self) -> str:
        if self.value is None:
            return "NOT_MEASURED"
        if self.sla is None:
            return "INFO"
        return "PASS" if self.sla.check(self.value) else "FAIL"


def fmt_value(m: Metric) -> str:
    if m.value is None:
        return "n/a"
    return f"{m.value:.4f}".rstrip("0").rstrip(".") if m.unit != "count" else f"{m.value:g}"
