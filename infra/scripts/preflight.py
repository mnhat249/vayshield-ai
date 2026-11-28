"""Preflight secret trước khi chạy compose prod / deploy. Chỉ stdlib. Không in giá trị bí mật.

Dùng:  python infra/scripts/preflight.py infra/prod.env      (exit 1 nếu có lỗi)

Kiểm: KEK + Ed25519 seed = base64 hợp lệ giải ra đúng 32 byte; peppers và JWT_OPS_SECRET >= 32 byte;
mật khẩu DB/Redis không rỗng và không phải mẫu; từ chối giá trị mẫu ("change-me", "DEV-ONLY").
``${VAR:?}`` của compose chỉ chặn biến rỗng, không thay được bước này.
"""

from __future__ import annotations

import base64
import binascii
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"change-me|changeme|dev-only|example\.invalid", re.IGNORECASE)
B64_32 = ("KMS_MASTER_KEK", "ED25519_POLICY_PRIVATE_KEY")
STR_32 = ("PEPPER_B2B_PHONE", "PEPPER_MULE_ACCOUNT", "JWT_OPS_SECRET")
PASSWORDS = ("DB_ADMIN_PASSWORD", "DB_PASSWORD", "REDIS_PASSWORD")
MIN_JUDGE_CODE = 16


def parse_env(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


def _b64_32(value: str) -> bool:
    padded = value + "=" * (-len(value) % 4)
    try:
        if "-" in value or "_" in value:
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        else:
            raw = base64.b64decode(padded.encode("ascii"), validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(raw) == 32 and not raw.upper().startswith(b"DEV-ONLY")


def check(env: dict[str, str]) -> list[str]:
    """Trả danh sách lỗi (chỉ tên biến + lý do, KHÔNG kèm giá trị)."""
    errors: list[str] = []
    for key in B64_32:
        value = env.get(key, "")
        if not value:
            errors.append(f"{key}: thiếu")
        elif PLACEHOLDER.search(value):
            errors.append(f"{key}: là giá trị mẫu")
        elif not _b64_32(value):
            errors.append(f"{key}: phải là base64 của đúng 32 byte")
    for key in STR_32:
        value = env.get(key, "")
        if not value:
            errors.append(f"{key}: thiếu")
        elif PLACEHOLDER.search(value):
            errors.append(f"{key}: là giá trị mẫu")
        elif len(value.encode("utf-8")) < 32:
            errors.append(f"{key}: phải >= 32 byte")
    for key in PASSWORDS:
        value = env.get(key, "")
        if not value:
            errors.append(f"{key}: thiếu")
        elif PLACEHOLDER.search(value):
            errors.append(f"{key}: là giá trị mẫu")
    redis_pw = env.get("REDIS_PASSWORD", "")
    if redis_pw and not re.fullmatch(r"[A-Za-z0-9]+", redis_pw):
        errors.append("REDIS_PASSWORD: chỉ được gồm [A-Za-z0-9] (nằm trong REDIS_URL)")
    judge = env.get("JUDGE_OPS_QUICK_LOGIN_CODE", "")
    if judge and len(judge) < MIN_JUDGE_CODE:
        errors.append(f"JUDGE_OPS_QUICK_LOGIN_CODE: < {MIN_JUDGE_CODE} ký tự (backend sẽ tắt)")
    cors = env.get("CORS_ORIGINS", "")
    if (
        not cors
        or "*" in cors
        or not all(o.strip().startswith("https://") for o in cors.split(","))
    ):
        errors.append("CORS_ORIGINS: bắt buộc danh sách https://..., không dùng *")
    domain = env.get("PUBLIC_DOMAIN", "")
    if not domain or domain.endswith(".invalid"):
        errors.append("PUBLIC_DOMAIN: thiếu hoặc là giá trị mẫu")
    if env.get("FORWARDED_ALLOW_IPS", "") == "*":
        errors.append("FORWARDED_ALLOW_IPS: không được là *")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: preflight.py <env-file>")
        return 2
    errors = check(parse_env(Path(args[0]).read_text(encoding="utf-8")))
    for err in errors:
        print(f"FAIL {err}")
    print(f"preflight: {len(errors)} loi")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
