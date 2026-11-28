"""Probe /healthz — chỉ dùng thư viện chuẩn (chạy được trong image python:slim trần).

- Mặc định: gọi 1 lần, thoát 0 nếu HTTP 200 (liveness). ``/healthz`` luôn 200 kể cả khi degraded.
- ``--ready``: thoát 0 chỉ khi body ``status == "ok"`` (readiness chặt: DB + Redis + registry/trust
  đã nạp + khóa ký sẵn sàng; "degraded" = chưa sẵn sàng nhận lưu lượng).
- ``--loop``: giữ ấm mỗi PROBE_INTERVAL_S giây (mặc định 180 — File 6 §1.2 mục 1, EC-OPS11).
  /healthz đã chạy ``SELECT 1`` PG + ``PING`` Redis nên mỗi nhịp giữ ấm kết nối
  PgBouncer/serverless.

Không in body (tránh lọt dữ liệu vào log). Không gọi Gemini ở đây (tốn ngân sách).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8000/healthz"
DEFAULT_INTERVAL_S = 180.0


def check(url: str, *, ready: bool, timeout_s: float = 5.0) -> tuple[bool, str]:
    """Trả (ok, lý do ngắn). Chỉ cho phép http/https."""
    if not url.startswith(("http://", "https://")):
        return False, "url_scheme_invalid"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310  # nosec B310
            status = resp.status
            raw = resp.read(65536)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, f"unreachable:{type(exc).__name__}"
    if status != 200:
        return False, f"http_{status}"
    if not ready:
        return True, "live"
    try:
        body = json.loads(raw)
    except ValueError:
        return False, "body_not_json"
    state = body.get("status") if isinstance(body, dict) else None
    return (state == "ok"), f"status={state}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("PROBE_URL", DEFAULT_URL))
    parser.add_argument("--ready", action="store_true")
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
    if not args.loop:
        ok, reason = check(args.url, ready=args.ready)
        print(reason)
        return 0 if ok else 1
    interval = float(os.environ.get("PROBE_INTERVAL_S", DEFAULT_INTERVAL_S))
    while True:
        ok, reason = check(args.url, ready=args.ready)
        print(f"probe ok={ok} {reason}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
