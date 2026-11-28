# syntax=docker/dockerfile:1.7
# VayShield AI backend — image production, KIẾN TRÚC linux/amd64 (EC-OPS04: Cloud Run chạy amd64;
# build trên Mac M1/M2/M3 vẫn ra amd64 nhờ --platform cố định ở FROM).
# Build:  docker buildx build --platform linux/amd64 -f backend/Dockerfile backend
# Context = thư mục backend/. Chỉ COPY có chọn lọc (không kéo .venv/tests/__pycache__ vào image).

# ---- builder: cài dependency đúng uv.lock (EC-OPS19: --frozen, không tự nâng phiên bản) ----------
FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime ------------------------------------------------------------------------------------
FROM --platform=linux/amd64 python:3.12-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Không root (uid 10001). Ảnh xử lý hoàn toàn trên RAM io.BytesIO, không ghi đĩa (EC-OPS13).
RUN groupadd --system --gid 10001 vayshield \
 && useradd --system --uid 10001 --gid vayshield --home-dir /app --shell /usr/sbin/nologin vayshield

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY ai_pipeline ./ai_pipeline
COPY migrations ./migrations
COPY seeds ./seeds

# Entrypoint tạo ngay trong Dockerfile (nguồn LF tuyệt đối). Vẫn chạy sed khử CR để miễn nhiễm
# nếu sau này đổi sang COPY một file sửa trên Windows (EC-OPS03).
#
# Proxy headers: uvicorn CHỈ tin X-Forwarded-For từ IP nằm trong FORWARDED_ALLOW_IPS (mặc định
# chỉ 127.0.0.1). Backend lấy IP rate-limit từ request.client, nên nếu KHÔNG cấu hình allowlist
# đúng dải IP của proxy thì mọi người dùng gom về IP proxy (1 bucket) — xem infra/README.md.
# KHÔNG đặt "*" khi cổng container còn truy cập được trực tiếp (kẻ gian giả XFF).
COPY <<'EOF' /usr/local/bin/entrypoint.sh
#!/bin/sh
set -eu
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" \
  --timeout-keep-alive "${UVICORN_KEEPALIVE_S:-75}" \
  --no-server-header \
  --workers "${WEB_CONCURRENCY:-1}"
EOF
RUN sed -i 's/\r$//g' /usr/local/bin/entrypoint.sh \
 && chmod 0755 /usr/local/bin/entrypoint.sh \
 && chmod -R a-w,a+rX /app
# /app thuộc root, chỉ đọc: user runtime (vayshield) không sửa được mã, chỉ ghi vào /tmp (tmpfs).

USER vayshield
EXPOSE 8000

# Liveness: /healthz luôn 200 (status=degraded nếu DB/Redis hỏng hoặc registry/trust chưa nạp).
# Readiness chặt (status == "ok") kiểm bằng infra/scripts/probe.py --ready (cron/CI).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8000'), timeout=4).read()"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
