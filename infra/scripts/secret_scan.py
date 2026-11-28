"""Quét secret bằng Python (DỰ PHÒNG khi máy không có binary gitleaks) — EC-OPS01.

KHÔNG thay thế gitleaks: chỉ dùng chính các rule/allowlist riêng trong ``.gitleaks.toml`` (rule
mặc định của gitleaks như AWS/private key không có ở đây, trừ vài mẫu thô bên dưới). Dùng làm cổng
nhanh trước khi nén .zip; bản nộp bài vẫn phải chạy ``gitleaks detect``.

Dùng:  python infra/scripts/secret_scan.py [--root .]   (exit 1 nếu thấy secret). Không in giá trị
secret, chỉ in ``đường_dẫn:dòng:rule``.
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 2_000_000
# Mẫu thô bổ sung (ngoài .gitleaks.toml) cho các dạng phổ biến.
BUILTIN: dict[str, str] = {
    "private-key-block": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "aws-access-key-id": r"\bAKIA[0-9A-Z]{16}\b",
}
FORBIDDEN_FILE = re.compile(r"(^|/)\.env(\..+)?$")
ALLOWED_ENV_FILES = re.compile(r"(^|/)\.env\.example$")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


@dataclass(frozen=True)
class Config:
    rules: dict[str, re.Pattern[str]]
    allow_paths: tuple[re.Pattern[str], ...]
    allow_values: tuple[re.Pattern[str], ...]


def load_config(toml_path: Path) -> Config:
    data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    rules = {name: re.compile(rx) for name, rx in BUILTIN.items()}
    for rule in data.get("rules", []):
        rules[rule["id"]] = re.compile(rule["regex"])
    allow = data.get("allowlist", {})
    return Config(
        rules=rules,
        allow_paths=tuple(re.compile(p) for p in allow.get("paths", [])),
        allow_values=tuple(re.compile(r) for r in allow.get("regexes", [])),
    )


def scan_text(path: str, text: str, cfg: Config) -> list[Finding]:
    if any(p.search(path) for p in cfg.allow_paths):
        return []
    found: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for rule_id, rx in cfg.rules.items():
            for m in rx.finditer(line):
                if any(a.search(m.group(0)) for a in cfg.allow_values):
                    continue
                found.append(Finding(path, lineno, rule_id))
    return found


def list_files(root: Path) -> list[str]:
    out = subprocess.run(  # nosec B603
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8", "replace")
    return [p for p in out.split("\0") if p]


def scan_repo(root: Path, cfg: Config) -> list[Finding]:
    findings: list[Finding] = []
    for rel in list_files(root):
        if FORBIDDEN_FILE.search(rel) and not ALLOWED_ENV_FILES.search(rel):
            findings.append(Finding(rel, 0, "env-file-in-repo"))
            continue
        full = root / rel
        try:
            if not full.is_file() or full.stat().st_size > MAX_BYTES:
                continue
            text = full.read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        findings.extend(scan_text(rel, text, cfg))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--config", default=".gitleaks.toml")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    cfg = load_config(root / args.config)
    findings = scan_repo(root, cfg)
    for f in findings:
        print(f"{f.path}:{f.line}:{f.rule}")
    print(f"secret_scan: {len(findings)} findings")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
