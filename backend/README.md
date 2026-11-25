# VayShield AI — Backend (FastAPI, Python 3.12)

## Chạy nhanh

```bash
cd backend
uv sync                                   # cài đúng phiên bản trong uv.lock
uv run uvicorn app.main:app --reload      # http://localhost:8000/healthz , /docs
uv run pytest -q                          # test Postgres (@pytest.mark.pg) tự skip nếu thiếu TEST_DATABASE_URL
uv run ruff check . && uv run ruff format --check . && uv run mypy .
```

Có Docker: `docker compose up -d` ở gốc repo (Postgres 16 + pgvector, Redis 7, backend) rồi
`curl -s localhost:8000/healthz`.

## Hợp đồng API (khóa ở mốc M0)

| File | Nội dung |
| --- | --- |
| `contracts/openapi.yaml` | OpenAPI 3.1 — 18 path của File 4 §4.1 + 7 bổ sung có căn cứ đặc tả |
| `contracts/sse-events.schema.json` | 5 pha SSE `/audits/contract-stream` + `ping` 2.5s + `audit.error`; live feed Ops |
| `contracts/ws-protocol.json` | Khung tin `/ws/v1/guardian`, `CLIENT_ALERT_ACK`, chuỗi leo thang, close code |

Sinh lại từ code: `uv run python -m app.core.contract_export` (`--check` để chỉ so sánh).
Nguồn model: `app/core/schemas/**`. Đổi contract sau M0 = task riêng có người duyệt.

Quy ước: tiền là **int VND**; `apr_*`/`ear_xirr` là **%/năm**; lỗi luôn là
`{"error": {"code", "message_vi", "trace_id", "details?"}}` (EC-OPS23); mỗi response có `X-Trace-Id`.

## Quy tắc cho task sau (D5)

- **Không sửa**: `pyproject.toml`, `uv.lock`, `app/main.py`, `app/core/**`, `app/api/router.py`,
  `tests/conftest.py`, `contracts/**`. Cần dependency / biến môi trường mới → ghi "Cần quyết định".
- Route stub nằm trong `app/api/routes/<module>.py`, mang `@stub` và trả mock từ `app/core/mocks.py`.
  Khi thay bằng logic thật: **giữ nguyên** path, method, `operation_id`, `response_model`,
  status code, tham số query/path/header và security dependency (`app/core/auth_headers.py`),
  rồi **bỏ `@stub`**. `tests/contract/test_openapi_matches.py` sẽ chặn mọi lệch cấu trúc.
- Xác thực: route **chỉ** dùng các dependency `Security(...)` có sẵn trong
  `app/core/auth_headers.py` (`DeviceCaller`, `DeviceOrSandboxCaller`, `SandboxCaller`,
  `GuardianCaller`, `RealtimeCaller`, `OpsCaller`, `PartnerCaller`). Kiểm tra mật mã (ECDSA thiết
  bị, HMAC B2B, JWT, vé) đặt ở `app/security` và đọc header/raw body qua `Request` —
  **không khai thêm `Header(...)`, `Security(...)` hay scheme mới** (sẽ lệch contract, test
  `test_openapi_matches` chặn).
- Vé 1 lần (WS `/ws/v1/guardian`, SSE `/ops/live-feed`) dài 16..128 ký tự. WS vé sai: accept →
  `SERVER_ERROR` → close **4401**. SSE live feed vé sai: 401 JSON.
- Body bị giới hạn ở tầng ASGI (`app/core/body_limit.py`, EC-DB05): mặc định 1 MiB,
  contract-stream ~6.26 MB, voice-terms ~5.26 MB (Settings `BODY_LIMIT_*`) → 413 JSON.
- Route SSE không `Depends(get_db)` (EC-DB01): dùng `app.core.db.short_session()`.
- Dùng `app.core.sse.format_sse` + `with_keepalive` + `SSE_HEADERS` cho mọi luồng SSE.
- Thời gian: nhận `Clock` (`app.core.clock`); test dùng fixture `fake_clock`.
- Test đặt trong `tests/<area>/` của task; fixture chung: `client`, `app`, `redis` (fakeredis + Lua),
  `pg` (skip nếu thiếu `TEST_DATABASE_URL`), `fake_clock`, `device_headers`, `sandbox_headers`.

## PostgreSQL: 2 role (bắt buộc cho RLS — EC-DB15)

| Role | Thuộc tính | Dùng cho |
| --- | --- | --- |
| `vayshield_admin` | superuser / owner schema | CHỈ chạy migration (`db-init` trong compose, bước CI) |
| `vayshield_app` | `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE` | App (`DATABASE_URL`) và test (`TEST_DATABASE_URL`, fixture `pg`) |

- Role `vayshield_app` được tạo TRƯỚC migration (compose `db-init`, CI step), chỉ có
  `CONNECT` + `USAGE ON SCHEMA public`. **Migration T01 phải tự `GRANT` tường minh** quyền tối
  thiểu cho `vayshield_app` trên từng bảng/sequence (vd không GRANT UPDATE/DELETE trên
  `tamper_evident_audit_chain`), và bật `ENABLE` + `FORCE ROW LEVEL SECURITY` trên bảng đa tenant.
- Test cần DDL/migration dùng fixture `pg_admin` (`TEST_ADMIN_DATABASE_URL`); test RLS luôn dùng
  `pg` (role app). Test `test_ec_db15_test_role_cannot_bypass_rls` chặn cấu hình sai.
- Migration chạy theo thứ tự tên file `backend/migrations/*.sql` bằng role admin.

## Biến môi trường

Toàn bộ khai báo ở `app/core/config.py` (tên biến = tên field viết hoa). Nhóm chính:
`APP_ENV`, `DATABASE_URL`, `REDIS_URL`, `GEMINI_API_KEYS`, `GEMINI_VIP_KEYS`,
`PEPPER_B2B_PHONE(_PREV,_VERSION)`, `PEPPER_MULE_ACCOUNT(_PREV,_VERSION)`, `KMS_MASTER_KEK(_PREV)`,
`ED25519_POLICY_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`,
`SMS_*`, `EMAIL_*`, `CORS_ORIGINS`, `SANDBOX_*`, `JWT_OPS_*`, `JUDGE_OPS_QUICK_LOGIN_CODE`,
`WS_*`, `ESCALATION_ACK_TIMEOUT_S`, `SSE_KEEPALIVE_INTERVAL_S`, `RATE_LIMIT_*`, `UPLOAD_*`.
Danh sách là CSV (`GEMINI_API_KEYS=k1,k2`). `APP_ENV=production` bắt buộc đủ bí mật, cấm
`CORS_ORIGINS=*` và `EXPOSE_ERROR_DETAILS=true`.

## Cấu trúc

```
app/
  main.py               create_app(), /healthz, CORS, middleware trace + catch-all
  core/                 config, db, redis, errors, logging (che PII), sse, clock, ids,
                        verdict_codes, auth_headers, openapi, schemas/**, mocks, stubs,
                        contract_export
  api/router.py         include 10 route module
  api/routes/*.py       devices policies audits shield family ws triage b2b ops sandbox
  finance/ verdict/ security/ shield_state/ services/{audit_pipeline,realtime}/
  mule_graph/ copilot/ triage/ sandbox/ db_schema/
ai_pipeline/{vision,extraction,ontology,legal_rag}/
tests/{contract,core,db,finance,ai_pipeline/*,shield_state,seeds,verdict,security,
       api/{audits,shield},mule_graph,copilot,sandbox}/
```
