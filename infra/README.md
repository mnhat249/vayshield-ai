# infra/ — hạ tầng VayShield AI (T18)

Chỉ là cấu hình. Không file nào ở đây tự deploy; deploy/push image/tạo tài nguyên cloud do người vận hành làm.

| File | Mục đích |
| --- | --- |
| `../docker-compose.yml` | 1 lệnh cho Giám khảo: `docker compose up --build` (demo local, secret giả) |
| `docker-compose.prod.yml` | VPS: Caddy 443 + backend + PG + Redis + keepwarm; secret bắt buộc qua env |
| `docker-compose.dev.yml` | Dev hot-reload (từ T00) |
| `Caddyfile` | TLS, SSE không gom gói, `www` -> domain chuẩn 308 |
| `cloudrun/service.yaml` | Cloud Run: `minScale=1`, `cpu-throttling=false`, probe `/healthz` |
| `railway/railway.json` | Railway: 1 replica, không sleep, healthcheck `/healthz` |
| `scripts/probe.py` | Liveness/readiness/keep-warm 180s (stdlib) |
| `scripts/secret_scan.py` | Quét secret dự phòng khi chưa có gitleaks |
| `ci/release-image.yml.sample` | Mẫu workflow build `linux/amd64` (KHÔNG push) |
| `env.sample` | Mẫu biến môi trường (giá trị giả) |

## Health / readiness
`GET /healthz` luôn trả 200; `status` là `ok` hoặc `degraded` (DB/Redis hỏng, hoặc registry nhà cho vay /
trust APK / khóa ký Ed25519 chưa nạp — fail-closed). Liveness = HTTP 200; readiness chặt =
`python infra/scripts/probe.py --ready` (exit 0 chỉ khi `status == "ok"`).

## Proxy tin cậy và rate-limit theo IP (QUAN TRỌNG)
Backend KHÔNG tự đọc `X-Forwarded-For`; rate-limit IP dùng `request.client`. Uvicorn chỉ thay
`request.client` bằng IP thật khi kết nối đến từ IP trong `FORWARDED_ALLOW_IPS` (mặc định `127.0.0.1`).
- Không đặt `FORWARDED_ALLOW_IPS` đúng dải proxy => mọi người dùng gom về IP proxy, dùng chung 1 bucket
  (`RATE_LIMIT_IP_RPM`, sandbox theo IP) và dễ tự khóa nhau.
- Đặt `*` khi cổng container còn truy cập trực tiếp => kẻ gian giả `X-Forwarded-For` để né rate-limit.
- Compose prod cố định subnet `172.28.10.0/24` và đặt allowlist đúng dải đó. Với Cloud Run/Railway phải
  tự xác minh dải IP proxy của nền tảng rồi điền `FORWARDED_ALLOW_IPS`.

## Việc người vận hành phải làm thủ công
1. Tạo secret thật (Secret Manager / `infra/prod.env`), không commit.
2. Trỏ DNS domain, cấu hình `PUBLIC_DOMAIN`, `CORS_ORIGINS` (canonical, không `www`).
3. Đăng ký Cloudflare Turnstile (nếu dùng) và nạp khóa theo cấu hình đã duyệt.
4. Build `docker buildx build --platform linux/amd64`, push image, `gcloud run services replace ...`.
5. Chạy `gitleaks detect --config .gitleaks.toml` trước khi nén `.zip`; chạy k6 trên staging.
6. DB serverless (Neon/Supabase): dùng gói không ngủ đông + giữ `keepwarm` 180s (EC-OPS11).
