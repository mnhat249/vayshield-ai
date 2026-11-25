---
slug: backend-vayshield
title: Toàn bộ nhiệm vụ Backend (Nhóm 1) — VayShield AI
status: approved            # draft → approved → dispatched → reviewed → integrated
base_branch: main
created: 2026-11-25
owner: Backend (Nhóm 1 — Core Backend, Neuro-Symbolic AI, Data & Cloud)
progress_file: .claude/plans/backend-vayshield/PROGRESS.md
spec_refs: [File 1 §1.2 §6, File 2 §1–§5, File 3 §1–§7, File 4 §1–§6, File 6 §1–§2 §7, team_division §2 §4]
work_packages: [WP1-01, WP1-02, WP1-03, WP1-04, WP1-05, WP1-06, WP1-07, WP1-08, WP1-09, WP1-10, WP1-11, WP1-12]
---

# Plan: Toàn bộ nhiệm vụ Backend — VayShield AI

> Ký hiệu đặc tả: **File N** = `momo/session_0N_*.md`. Edge case của File 2 không có tiền tố trong tài liệu gốc → plan gọi là `S2-EC01..48`.
> Các mã khác giữ nguyên: `EC-S..`, `EC-AI..`, `EC-DB..`, `EC-OPS..`.

## 1. Mục tiêu & phạm vi
- **Mục tiêu:** xây toàn bộ phần backend của VayShield AI theo 12 work package `WP1-01..12` trong `momo/team_division_2_groups.md` §2. Đó là FastAPI, lõi toán tài chính tất định, pipeline AI, PostgreSQL/Redis, realtime WS/Push, B2B, Sandbox, benchmark và hạ tầng cloud. Kết quả phải chạy thật, nối được với Web/Android của Nhóm 2 qua `contracts/`.
- **Ngoài phạm vi (KHÔNG làm):** `web-portal/`, `android-app/`, `submission-docs/` (Nhóm 2). Logic chỉ chạy trên thiết bị (Zero-OTP gate, SQLCipher, InterceptionActivity), nhưng backend phải cung cấp đủ API/policy mà chúng cần.

## 2. Quyết định đã chốt
- **D1 — Stack:** Python 3.12, FastAPI (async), Pydantic v2, PostgreSQL 16 + `pgvector` + `pg_trgm` + `unaccent`, Redis 7 (Lua), Gemini 2.5 Flash/Pro (File 1 ADR-04, File 6 §5.2). Quản lý gói bằng `uv` + `uv.lock` (EC-OPS19).
- **D2 — Neuro-symbolic:** LLM chỉ trích xuất JSON. Mọi phép tính lãi và mọi phán quyết do kernel tất định Python làm. LLM không được hạ mức cảnh báo (File 2 §1.1, File 3 §2.2).
- **D3 — Tiền tệ:** kernel dùng `Decimal`/int đồng ở biên vào/ra (CLAUDE.md §4). Solver nội bộ được dùng float, nhưng đầu ra làm tròn có kiểm soát và có test dung sai (File 2 §4.2 tol 1e-8).
- **D4 — Một nguồn sự thật cho API:** `contracts/openapi.yaml` + `sse-events.schema.json` + `ws-protocol.json` do T00 tạo và khóa ở M0. Mọi route phải khớp contract; có test so khớp.
- **D5 — Chống xung đột khi song song:** T00 tạo sẵn khung cho mọi file dùng chung: `pyproject.toml` khai đủ dependency, `app/core/config.py` khai đủ biến môi trường, `app/api/router.py` include sẵn mọi route module, `tests/conftest.py`. Task sau **không sửa các file này**. Cần thêm dependency hoặc biến môi trường thì ghi vào "Cần quyết định".
- **D6 — Không gọi mạng thật trong test:** Gemini, Web Push, SMS đều qua interface + fake.
- **D6b — Môi trường:** máy dev KHÔNG có Docker/Postgres/Redis. Chạy Python bằng `uv` (đã cài, Python 3.12). Test Redis dùng `fakeredis[lua]`. Test cần Postgres thật đánh dấu `@pytest.mark.pg` và tự skip khi không có `TEST_DATABASE_URL`; chúng chạy thật trong GitHub Actions (`.github/workflows/backend-ci.yml`, service postgres pgvector + redis) do T00 tạo. Mọi lệnh chạy trong `backend/`: `uv run pytest`, `uv run ruff`, `uv run mypy`.
- **D7 — Sandbox:** mọi dữ liệu có `sandbox_namespace`, tách khỏi `live` ở tầng repository/RLS (File 4 EC-DB16, File 2 S2-EC28).

## 3. Hợp đồng chung (phải có TRƯỚC wave 1)
- `contracts/openapi.yaml`: đủ 16 endpoint ở File 4 §4.1 (devices, policies, audits×4, shield×3, family×3, triage, ws, b2b, ops×2, sandbox).
- `contracts/sse-events.schema.json`: 5 pha SSE của `/audits/contract-stream` (File 4 §4.2(A)) + event `ping` mỗi 2.5s (EC-DB22).
- `contracts/ws-protocol.json`: khung tin WebSocket + `CLIENT_ALERT_ACK` + chuỗi leo thang (File 4 §4.2(C)).
- Mã phán quyết dùng chung: 9 nhãn `V_*` (File 2 §4.4) + `INFINITE_APR_ADVANCE_FEE_SCAM`. Tạo ở T00, đặt trong `backend/app/core/verdict_codes.py` và trong contract.
- ⚠ Contract dùng chung với Nhóm 2 → **người duyệt + Nhóm 2 đồng ý** trước khi đóng T00 (mốc M0).

## 4. Cấu trúc thư mục & vùng sở hữu

```
contracts/                         T00
docker-compose.yml                 T00 (dev) → T18 (prod-ready)
backend/
  pyproject.toml, uv.lock          T00 (khóa)
  app/main.py, app/core/**         T00 (khóa)
  app/api/router.py                T00 (khóa)
  app/api/routes/devices.py, policies.py, audits.py      T11
  app/api/routes/shield.py, family.py, ws.py             T12
  app/api/routes/b2b.py                                  T14
  app/api/routes/ops.py, triage.py                       T15
  app/api/routes/sandbox.py                              T16
  app/finance/**        T02     app/verdict/**       T09
  app/security/**       T10     app/shield_state/**  T05 (gồm lua/)
  app/services/audit_pipeline/**  T11
  app/services/realtime/**        T12
  app/mule_graph/**     T14     app/copilot/**, app/triage/**  T15
  app/sandbox/**        T16
  ai_pipeline/vision/** T03     ai_pipeline/extraction/**  T04
  ai_pipeline/ontology/** T07   ai_pipeline/legal_rag/**   T13
  migrations/0001_*.sql T01     migrations/0002_*.sql, seeds/**  T08
  tests/<area>/**       mỗi task một thư mục con (vd tests/finance/ của T02)
benchmark-dataset/data/**, labels/**, schema/**   T06
benchmark-dataset/runner/**, results/**           T17
infra/**, backend/Dockerfile, .gitleaks.toml, loadtest/**   T18
```

## 5. Danh sách task

| ID | Tên | WP | Wave | Phụ thuộc | Vùng sở hữu chính | Model |
|----|-----|----|------|-----------|-------------------|-------|
| T00 | Khung FastAPI + contracts M0 | WP1-06 | 0 | – | `contracts/**`, `backend/{pyproject.toml,uv.lock,app/main.py,app/core/**,app/api/router.py,app/api/routes/*.py (stub)}`, `docker-compose.yml`, `backend/tests/conftest.py` | opus |
| T01 | PostgreSQL schema + RLS + hash-chain | WP1-01 | 1 | T00 | `backend/migrations/0001_*.sql`, `backend/app/db_schema/**`, `backend/tests/db/**` | sonnet |
| T02 | Kernel tài chính APR/XIRR/khoảng/MIRR | WP1-03 | 1 | T00 | `backend/app/finance/**`, `backend/tests/finance/**` | opus |
| T03 | Tiền xử lý thị giác đối kháng + VietQR | WP1-04 | 1 | T00 | `backend/ai_pipeline/vision/**`, `backend/tests/ai_pipeline/vision/**` | sonnet |
| T04 | Trích xuất LLM + Conformal + Voice-to-Cashflow | WP1-05 | 1 | T00 | `backend/ai_pipeline/extraction/**`, `backend/tests/ai_pipeline/extraction/**` | opus |
| T05 | Redis Bayesian HSM + Lua chống smurfing + idempotency | WP1-07 | 1 | T00 | `backend/app/shield_state/**`, `backend/tests/shield_state/**` | opus |
| T06 | Bộ dữ liệu VayShield-Bench-340 (data + nhãn) | WP1-11 | 1 | T00 | `benchmark-dataset/{data,labels,schema}/**`, `benchmark-dataset/README.md` | sonnet |
| T07 | Bộ phân loại 10 trục ontology C1–C10 | WP1-08 | 1 | T00 | `backend/ai_pipeline/ontology/**`, `backend/tests/ai_pipeline/ontology/**` | sonnet |
| T08 | Seed registry 16 FinCo + corpus luật + mule mẫu | WP1-02 | 2 | T01 | `backend/migrations/0002_*.sql`, `backend/seeds/**`, `backend/tests/seeds/**` | sonnet |
| T09 | Ma trận 9 phán quyết + lookalike + chỉ số sinh kế | WP1-03 | 2 | T02, T01 | `backend/app/verdict/**`, `backend/tests/verdict/**` | opus |
| T10 | Mật mã & bảo mật nền (envelope, blind index, Ed25519, device auth, rate limit) | WP1-06 | 2 | T01 | `backend/app/security/**`, `backend/tests/security/**` | opus |
| T11 | API Trạm 1 & 2 (apk-verify, SSE contract-stream, voice, recalculate, devices, policies) | WP1-06 | 3 | T02,T03,T04,T09,T10 | `backend/app/api/routes/{devices,policies,audits}.py`, `backend/app/services/audit_pipeline/**`, `backend/tests/api/audits/**` | opus |
| T12 | API Trạm 3 + WS hub + leo thang WS→Push→SMS + ghép đôi gia đình | WP1-08 | 3 | T05,T07,T10 | `backend/app/api/routes/{shield,family,ws}.py`, `backend/app/services/realtime/**`, `backend/tests/api/shield/**` | opus |
| T13 | Legal GraphRAG gắn với phán quyết | WP1-02 | 3 | T08 | `backend/ai_pipeline/legal_rag/**`, `backend/tests/ai_pipeline/legal_rag/**` | sonnet |
| T14 | Đồ thị Mule Pre-SIMO + Dynamic VA + webhook B2B | WP1-09 | 3 | T01,T10 | `backend/app/mule_graph/**`, `backend/app/api/routes/b2b.py`, `backend/tests/mule_graph/**` | opus |
| T15 | Copilot B2B + hồ sơ SIMO + triage Giờ Vàng + TTS | WP1-09 | 4 | T13,T14 | `backend/app/copilot/**`, `backend/app/triage/**`, `backend/app/api/routes/{ops,triage}.py`, `backend/tests/copilot/**` | sonnet |
| T16 | Sandbox Mode backend + VIP quota + budget breaker | WP1-10 | 4 | T11,T12 | `backend/app/sandbox/**`, `backend/app/api/routes/sandbox.py`, `backend/tests/sandbox/**` | sonnet |
| T17 | Runner benchmark + 3 thí nghiệm ablation | WP1-11 | 4 | T06,T11,T12 | `benchmark-dataset/{runner,results}/**` | sonnet |
| T18 | Hạ tầng cloud zero-cold-start + Docker + gitleaks + k6 | WP1-12 | 5 | tất cả | `infra/**`, `backend/Dockerfile`, `docker-compose.yml`, `.gitleaks.toml`, `loadtest/**`, `.gitattributes` | sonnet |

> Song song: wave 1 có 7 task. `/dispatch-tasks` chạy tối đa 4 task một lượt (T01–T04 trước, rồi T05–T07).
> Wave 2: 3 task. Wave 3: 4 task. Wave 4: 3 task. Wave 5: 1 task.

---

### T00 — Khung FastAPI + hợp đồng M0
- **branch:** `task/backend-vayshield/T00`
- **owns:** `contracts/**`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/core/**`, `backend/app/api/router.py`, `backend/app/api/routes/*.py` (chỉ stub), `backend/ai_pipeline/__init__.py`, `backend/tests/conftest.py`, `backend/tests/contract/**`, `docker-compose.yml`, `.env.example`, `backend/README.md`, `.github/workflows/backend-ci.yml`
- **spec:** File 4 §4.1, §4.2(A)(B)(C); File 2 §4.4 (mã phán quyết); File 6 §1.2 (port 443, `X-Accel-Buffering: no`); team_division §1, §4 (M0)
- **Yêu cầu:**
  1. Tạo `contracts/openapi.yaml` (OpenAPI 3.1) với đủ 16 endpoint, schema request/response, mã lỗi chuẩn (EC-OPS23: không bao giờ trả 500 HTML, luôn trả JSON `{error:{code,message_vi}}`). Tạo `sse-events.schema.json` (5 pha + `ping`) và `ws-protocol.json`.
  2. Khung FastAPI: `app/main.py`, `core/config.py` (pydantic-settings, **khai báo đủ** mọi biến môi trường: DB, Redis, `GEMINI_API_KEYS`, `GEMINI_VIP_KEYS`, `PEPPER_B2B_PHONE`, `PEPPER_MULE_ACCOUNT` (+ `_PREV` để xoay khóa), `KMS_MASTER_KEK`, `ED25519_POLICY_PRIVATE_KEY`, `VAPID_*`, `SMS_*`, `CORS_ORIGINS`, `SANDBOX_*`), `core/db.py` (async SQLAlchemy/asyncpg), `core/redis.py`, `core/errors.py`, `core/verdict_codes.py`, `core/logging.py` (lọc PII/OTP).
  3. `api/router.py` include sẵn 10 route module. Mỗi stub trả **mock đúng contract** để Nhóm 2 dùng ngay (MSW/Scalar).
  4. `pyproject.toml` khai đủ dependency cho toàn plan (fastapi, uvicorn, pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, alembic hoặc runner SQL, redis, google-genai, pillow, opencv-python-headless, numpy, pyzbar hoặc zxing-cpp, rapidfuzz, cryptography, pynacl, pgvector, httpx, sse-starlette, pywebpush, pytest, pytest-asyncio, ruff, mypy, hypothesis). Khóa bằng `uv lock`.
  5. `docker-compose.yml` dev: postgres16 + pgvector, redis7, backend.
  5b. Tạo sẵn **mọi** package rỗng có `__init__.py` cho các thư mục ở mục 4: `app/{finance,verdict,security,shield_state,services,services/audit_pipeline,services/realtime,mule_graph,copilot,triage,sandbox,db_schema}`, `ai_pipeline/{vision,extraction,ontology,legal_rag}`, `tests/` và từng thư mục con. Nhờ vậy các task song song không cùng tạo một file `__init__.py`.
  6. Test contract: `tests/contract/test_openapi_matches.py` so app.openapi() với `contracts/openapi.yaml` (path + method + tên field).
- **DoD:** `uv run pytest -q` pass; `ruff`/`mypy` sạch; `docker compose up` có `/healthz` 200; mọi endpoint trả mock hợp lệ theo schema; người dùng + Nhóm 2 duyệt contract.
- **Verify:** `node .claude/scripts/run-checks.js all backend`; `docker compose up -d && curl -s localhost:8000/healthz`

### T01 — PostgreSQL 16 schema + RLS + hash-chain (WP1-01)
- **branch:** `task/backend-vayshield/T01`
- **owns:** `backend/migrations/0001_*.sql`, `backend/app/db_schema/**` (model/repository mỏng), `backend/tests/db/**`
- **spec:** File 4 §1 (4 vùng dữ liệu), §2.1 ERD, §2.2 DDL đầy đủ (extensions, 14 bảng, HNSW/GIN index, RLS `pol_b2b_tenant_isolation`, trigger `forbid_audit_chain_mutation`)
- **Yêu cầu:** chuyển DDL File 4 §2.2 thành migration chạy sạch. UUIDv7. Bảng nào có `sandbox_namespace` thì cột phải `NOT NULL DEFAULT 'live'`. Có CHECK giới hạn kích thước/độ sâu `masked_extraction_json`. Viết runner migration idempotent.
- **Edge cases:** EC-DB15 (RLS chặn đọc chéo tenant), EC-DB20 (UPDATE/DELETE audit chain bị chặn), EC-DB18 (JSON quá sâu/quá nặng bị từ chối), EC-DB26 (null byte), EC-DB17 (tham số HNSW `m=16, ef_construction` đúng spec)
- **DoD:** migration chạy 2 lần không lỗi; test RLS bằng 2 role tenant; test trigger chống sửa hash-chain; test hash-chain liên tục (prev_hash).
- **Verify:** `uv run pytest tests/db -q` (cần `docker compose up -d postgres`)

### T02 — Kernel tài chính tất định (WP1-03 phần 1)
- **branch:** `task/backend-vayshield/T02`
- **owns:** `backend/app/finance/**`, `backend/tests/finance/**`
- **spec:** File 2 §4.1 (vector CF, bất biến phí ứng trước), §4.2 (`solve_vn_legal_apr_365`, `solve_ear_xirr`, Brent kẹp `[-0.99, 500]`, `< 45` vòng), §4.2 mở rộng (đa chế độ/ân hạn, bán lại hàng hóa, MIRR 4.5%), §4.3 (C_loss, ngày công 320.000đ, xoáy nợ 30-60-90 ngày); File 6 §2 hàng 1 (35 pytest)
- **Yêu cầu:** `CashFlowPoint`, `build_cashflows(extraction)` (multi-regime builder, CF0 = nominal − deducted − bundled − upfront), `solve_vn_legal_apr_365`, `solve_ear_xirr`, `solve_apr_interval_bounds`, `solve_mirr`, kiểm tra Descartes đổi dấu, `livelihood_pain(...)`, `rollover_spiral(...)`. Hàm thuần, không I/O, mỗi lần gọi `< 1ms`.
- **Edge cases:** S2-EC12 (góp 250k×50 ngày → 351.8%/năm), S2-EC13 (flat vs dư nợ giảm), S2-EC14 (phí khấu trừ 25%), S2-EC15 (gia hạn 1.8tr/10tr/7 ngày → 938.6%), S2-EC39 (2 giai đoạn/ân hạn), S2-EC40 (đổi dấu → MIRR), S2-EC41 (khoảng bất định), S2-EC42 (Actual/365, dung sai ±0.5% tại mốc 20%); ví dụ chuẩn File 2 §4.2: nhận 8.5tr trả 12tr sau 30 ngày → APR 501.0%, XIRR 6.568,5%; ví dụ File 2 §1.3: 10tr/12 tháng/1.35tr → ~96.5%
- **DoD:** ≥ 35 test (File 6 QA hàng 1) + property test (hypothesis): APR(n=1) == công thức lãi đơn; benchmark `< 1ms`/lần.
- **Verify:** `uv run pytest tests/finance -q`

### T03 — Tiền xử lý thị giác đối kháng + giải mã VietQR (WP1-04)
- **branch:** `task/backend-vayshield/T03`
- **owns:** `backend/ai_pipeline/vision/**`, `backend/tests/ai_pipeline/vision/**`
- **spec:** File 3 §2.1 (5 lớp: khử chữ tàng hình theo phân cực sáng/tối, khử mộc đỏ HSV, VietQR TLV + phân định bong bóng trái/phải, ELA + bất biến NAPAS 247, NFKC/homoglyph); File 2 S2-EC34 (tiled slicer 1080×1800, chồng 15%); File 4 EC-DB05/06
- **Yêu cầu:** `sanitize_image()`, `strip_red_stamp()`, `tile_long_screenshot()`, `decode_vietqr()` (CRC16, Tag 38/54/62, quét injection trong Tag 62), `attribute_qr_sender()`, `receipt_forensics()` (`FAKE_RECEIPT_NAPAS_IMPOSSIBLE`), `canonicalize_text()`. Hàm nặng CPU phải gọi được qua threadpool (EC-OPS12).
- **Edge cases:** EC-AI01..06, EC-AI23, EC-AI25, S2-EC10, S2-EC19, S2-EC34, EC-DB05 (giới hạn pixel/decompression bomb), EC-DB06 (magic-byte, chặn SVG/polyglot)
- **DoD:** fixture ảnh tổng hợp sinh bằng code (không dùng ảnh thật có PII); mỗi EC có test.
- **Verify:** `uv run pytest tests/ai_pipeline/vision -q`

### T04 — Trích xuất LLM + Conformal escalation + Voice-to-Cashflow (WP1-05)
- **branch:** `task/backend-vayshield/T04`
- **owns:** `backend/ai_pipeline/extraction/**`, `backend/tests/ai_pipeline/extraction/**`
- **spec:** File 3 §1.2 (Flash → crop ROI → Pro, ngưỡng `c_f < 0.82`, lệch tổng > 2%, brand conflict), §1.3 (từ điển tiếng lóng "củ, chai, xị, lít, cành"), §2.2 (`MultiImageLoanExtraction` v2.0), §2.3 (tách đa khoản vay, thứ bậc màn hình, khử trùng phí), §2.4 (system prompt chống injection); File 2 §3.1 tầng 1–2, S2-EC46 (soft-timeout 4.5s, failover 6s)
- **Yêu cầu:** schema Pydantic đúng v2.0; `GeminiClient` interface + fake; `ConformalRouter`; `ModelCascade` (primary → secondary sau timeout, xoay vòng nhiều key); `MultiLoanDisentangler`; `normalize_vn_currency_slang()`; sửa JSON bị cắt cụt hoặc yêu cầu gọi lại (EC-AI22).
- **Edge cases:** EC-AI07..12, EC-AI14, EC-AI15, EC-AI22, EC-AI24, S2-EC18, S2-EC21, S2-EC46
- **DoD:** không gọi mạng thật trong test; có test cho prompt injection (lệnh trong ảnh không làm đổi số); có test cho từ lóng tiền tệ.
- **Verify:** `uv run pytest tests/ai_pipeline/extraction -q`

### T05 — Redis Bayesian HSM + Lua chống smurfing + idempotency (WP1-07)
- **branch:** `task/backend-vayshield/T05`
- **owns:** `backend/app/shield_state/**` (gồm `lua/atomic_smurf_and_risk_eval.lua`), `backend/tests/shield_state/**`
- **spec:** File 2 §2.1 (HSM 10 trạng thái, K=8 fiber, join-semilattice), §2.2 (log-odds, prior −3.5, trọng số + bán rã, hàm 2 pha 120 phút, Ψ synergy, Ξ proj, Ω smurf, γ multi 0.55, ngưỡng 0.35/0.75); File 4 §3.1 (keyspace `vs:*` + TTL), §3.2 (Lua)
- **Yêu cầu:** `compute_log_odds()` thuần (khớp công thức), `FiberSupervisor`, `transition()` theo bảng trạng thái, lưu/đọc Redis HASH, Lua nguyên tử (smurf ZSET 15m + đánh giá rủi ro), idempotency thông báo 10m và B2B 48h.
- **Edge cases:** S2-EC07, S2-EC08, S2-EC30, S2-EC31, S2-EC43 (race: lấy `MAX(shield_expires_at)` + gộp nguồn bằng chứng), EC-DB07 (lost update), EC-DB11 (Redis mất 5s → fallback đọc PG/trả trạng thái an toàn)
- **DoD:** test bảng chuyển trạng thái đầy đủ; test số học P(Scam) theo ví dụ spec (95.7% khi giải ngân + IN_CALL…); test Lua bằng Redis thật.
- **Verify:** `uv run pytest tests/shield_state -q`

### T06 — Bộ dữ liệu VayShield-Bench-340 (WP1-11 phần 1)
- **branch:** `task/backend-vayshield/T06`
- **owns:** `benchmark-dataset/data/**`, `benchmark-dataset/labels/**`, `benchmark-dataset/schema/**`, `benchmark-dataset/README.md`
- **spec:** File 3 §6.1 (4 tập: VS-Contract-80, VS-VoiceTerms-40, VS-Coercion-120, VS-Notif-100 + metric & SLA)
- **Yêu cầu:** schema nhãn JSON cho từng tập; sinh dữ liệu **tổng hợp** (không PII thật) bằng script tái lập được; nhãn ground-truth (APR kỳ vọng tính tay, verdict, trục C1–C10, OTP/không OTP).
- **DoD:** đủ 340 mẫu có nhãn hoặc ghi rõ phần còn thiếu + lý do; script validate nhãn pass.
- **Verify:** `uv run python benchmark-dataset/schema/validate.py`

### T07 — Bộ phân loại 10 trục thao túng C1–C10 (WP1-08 phần 1)
- **branch:** `task/backend-vayshield/T07`
- **owns:** `backend/ai_pipeline/ontology/**`, `backend/tests/ai_pipeline/ontology/**`
- **spec:** File 3 §3.1–§3.3 (hỏi đáp bất đối xứng, trộn thứ tự lựa chọn, 10 trục, C7 ngập ngừng > 1.8s khi IN_CALL + mở lại ≥ 2 lần)
- **Yêu cầu:** `generate_challenge(session)` (trộn lựa chọn theo phiên), `classify_response(text|choice, sensors)` → trục + hành động (RED/ORANGE-RED/PASS). Lớp luật tất định chạy trước, LLM chỉ là tùy chọn qua interface có fake.
- **Edge cases:** S2-EC22 (mớm lời), EC-AI12 (STT nghe nhầm), EC-AI13 (im lặng 2.5s), S2-EC47 (deepfake → thông điệp gọi lại hotline)
- **DoD:** mỗi trục C1–C10 có ≥ 3 test câu biến thể không dùng từ khóa gốc.
- **Verify:** `uv run pytest tests/ai_pipeline/ontology -q`

### T08 — Seed registry + corpus luật + mule mẫu (WP1-02)
- **branch:** `task/backend-vayshield/T08`
- **owns:** `backend/migrations/0002_*.sql`, `backend/seeds/**`, `backend/tests/seeds/**`
- **spec:** File 1 §1.2 (Ngân hàng + 16 FinCo + P2P Sandbox NĐ 94/2025, trần 100tr), File 3 §5.1 (node điều/khoản + cạnh `supersedes_or_exempts` + `applicable_verdicts`), File 6 EC-OPS16 (đồ thị không trống khi giám khảo mở)
- **Yêu cầu:** `licensed_lender_registry` (tên chuẩn, alias, `packageName`, SHA-256 cert nếu có, hotline chính thức, domain); `legal_provision_nodes/edges` cho BLDS 468, BLHS 201, NĐ 94/2025, TT 43/2016, TT 18/2019, Luật TCTD 2024 Đ15/Đ100, NQ 01/2021; 3 cụm mule mẫu trong namespace `sandbox_seed`.
- **⚠ Cần người xác minh:** hotline, `packageName` và nguyên văn điều luật phải lấy từ nguồn chính thức. Agent đánh dấu `verified=false` cho mục chưa kiểm chứng, **không bịa**.
- **DoD:** seed chạy idempotent; test số lượng + ràng buộc; danh sách mục `verified=false` trong báo cáo.
- **Verify:** `uv run pytest tests/seeds -q`

### T09 — Ma trận 9 phán quyết + lookalike + chỉ số sinh kế (WP1-03 phần 2)
- **branch:** `task/backend-vayshield/T09`
- **owns:** `backend/app/verdict/**`, `backend/tests/verdict/**`
- **spec:** File 2 §4.4 (9 nhãn + điều kiện), §1.3 chặng 4–5 (mâu thuẫn thương hiệu/STK/lãi, cổng đủ chứng từ), S2-EC16 (Levenshtein + Jaro-Winkler > 0.78), S2-EC17 (P2P > 100tr); File 1 §1.2
- **Yêu cầu:** `decide_verdict(extraction, apr_interval, lender_match, provenance) -> VerdictObject` (màu, mã, lý do, căn cứ luật id, tts_script); `LenderRegistry` interface (in-memory + repo PG); `detect_lookalike_brand()`; thứ tự ưu tiên khi nhiều luật cùng khớp có test.
- **Edge cases:** S2-EC13, S2-EC15, S2-EC16, S2-EC17, S2-EC41, EC-AI24; bảng 9 nhãn mỗi nhãn ≥ 2 test dương + 1 test biên
- **DoD:** bảng quyết định có test đầy đủ; LLM không thể đổi verdict (kiểm tra bằng test).
- **Verify:** `uv run pytest tests/verdict -q`

### T10 — Mật mã & bảo mật nền (WP1-06/09 dùng chung)
- **branch:** `task/backend-vayshield/T10`
- **owns:** `backend/app/security/**`, `backend/tests/security/**`
- **spec:** File 4 §5.1 (envelope AES-256-GCM per-record DEK, blind index HMAC dual-pepper, Ed25519 policy bundle, quorum + ECDSA P-256 thiết bị, memory hygiene), §5.2 (Luật 91/2025); File 2 §1.1 (SignedServerEpochOffset), S2-EC29
- **Yêu cầu:** `envelope.encrypt/decrypt`, `blind_index(kind, value)` với xoay pepper (`_PREV`), `sign_policy_bundle()`, `signed_epoch_offset()`, dependency FastAPI xác thực chữ ký thiết bị, one-time WS ticket helper, rate limiter Redis theo device + IP, chuẩn hóa SĐT E.164, CORS chặt, xóa dữ liệu theo yêu cầu (EC-DB21).
- **Edge cases:** EC-DB08, EC-DB10, EC-DB21, EC-DB24, EC-DB25 (NAT chung IP → rate limit theo device trước IP), S2-EC29
- **DoD:** test vòng mã hóa/giải mã, test xoay pepper vẫn tra được, test chữ ký sai bị 401, test log không chứa PII.
- **Verify:** `uv run pytest tests/security -q`

### T11 — API Trạm 1 & 2 (WP1-06)
- **branch:** `task/backend-vayshield/T11`
- **owns:** `backend/app/api/routes/{devices,policies,audits}.py`, `backend/app/services/audit_pipeline/**`, `backend/tests/api/audits/**`
- **spec:** File 4 §4.1 (SLA), §4.2(A) (SSE 5 pha, first event < 200ms, full < 3.5s); File 2 §1.3 (7 chặng), §3.2 (sequence Trạm 2), §3.1 (4 tầng hạ cấp: form 3 ô khi AI chết); File 6 §1.2 (`X-Accel-Buffering: no`)
- **Yêu cầu:** `POST /devices/register`, `GET /policies/latest` (ETag + Ed25519), `POST /audits/apk-verify`, `POST /audits/contract-stream` (pipeline 7 chặng: T03 → T04 → T09 + T02 → lưu audit đã che PII → SSE), `POST /audits/voice-terms`, `POST /audits/{id}/recalculate` (< 15ms, 0 LLM).
- **Edge cases:** EC-DB01 (không giữ connection DB khi stream), EC-DB22 (ping 2.5s), EC-DB26, EC-OPS12 (CPU-bound → threadpool), EC-OPS13 (tmp cleanup), EC-OPS23, S2-EC46, S2-EC20
- **DoD:** test SSE thứ tự 5 pha khớp `sse-events.schema.json`; test recalculate < 15ms; test fallback khi Gemini fake timeout; contract test pass.
- **Verify:** `uv run pytest tests/api/audits tests/contract -q`

### T12 — API Trạm 3 + WS hub + leo thang + ghép đôi gia đình (WP1-08 phần 2)
- **branch:** `task/backend-vayshield/T12`
- **owns:** `backend/app/api/routes/{shield,family,ws}.py`, `backend/app/services/realtime/**`, `backend/tests/api/shield/**`
- **spec:** File 4 §4.1, §4.2(C) (WS → chờ `CLIENT_ALERT_ACK` 10s → VAPID Push → SMS/Email), §3.1 keys `vs:pair:*`, `vs:ws:ticket:*`, `vs:ack:timer:*`, `vs:chan:family:*`; File 2 §3.3; File 1 ADR-03
- **Yêu cầu:** `POST /shield/arm`, `/shield/evaluate-response` (T07), `/shield/escalate`, `/family/pair-init`, `/pair-confirm`, `/family/send-rescue-card` (< 150ms), `GET /ws/v1/guardian?ticket=`; Redis pub/sub cho nhiều instance; payload ẩn số tiền (Zero-Amount Privacy Envelope).
- **Edge cases:** S2-EC23, S2-EC24 (mã 6 ký tự, TTL 10m, ≤ 3 lần sai), S2-EC44 (half-open → ACK timeout), S2-EC45 (gom cảnh báo cũ > 45 phút), EC-DB12 (ticket dùng lại bị từ chối), EC-DB13 (2 instance), EC-DB14 (Push 410 → bỏ subscription, chuyển SMS)
- **DoD:** test leo thang bằng fake clock; test 2 instance qua Redis thật; test payload không có số tiền/tên công ty vay.
- **Verify:** `uv run pytest tests/api/shield -q`

### T13 — Legal GraphRAG gắn với phán quyết (File 3 §5.1)
- **branch:** `task/backend-vayshield/T13`
- **owns:** `backend/ai_pipeline/legal_rag/**`, `backend/tests/ai_pipeline/legal_rag/**`
- **spec:** File 3 §5.1 (chiều 1: chỉ nạp node của verdict; chiều 2: hybrid 0.65 cosine + 0.35 BM25 + mở rộng 1-hop), EC-AI14/16/17, EC-DB17
- **Yêu cầu:** `explain_verdict(verdict_code) -> {plain_vi, citations[]}` (không bao giờ trích điều luật ngoài danh sách được phép); `hybrid_search(query)` cho Ops; lọc hiệu lực theo thời gian.
- **DoD:** test "zero legal hallucination": mọi citation đều thuộc `applicable_verdicts`; test văn bản hết hiệu lực không được trả về.
- **Verify:** `uv run pytest tests/ai_pipeline/legal_rag -q`

### T14 — Đồ thị Mule Pre-SIMO + Dynamic VA + webhook B2B (WP1-09 phần 1)
- **branch:** `task/backend-vayshield/T14`
- **owns:** `backend/app/mule_graph/**`, `backend/app/api/routes/b2b.py`, `backend/tests/mule_graph/**`
- **spec:** File 4 §4.2(B) (HMAC-SHA256 + timestamp ≤ 300s + `Idempotency-Key` 48h, blind index SĐT), §2.2 bảng `mule_account_nodes/incident_edges/fraud_campaign_clusters/b2b_disbursement_shields`; File 3 §5.2 (Huber/spherical medoid, quorum ≥ 2 thiết bị trong 6h); File 2 S2-EC26/27/43
- **Yêu cầu:** `POST /b2b/disbursement-shield` → bật khiên 24h qua T05; ghi nhận STK nghi vấn (envelope + blind index); gom cụm `va_prefix_hmac` cho tài khoản ảo động; quorum nâng `Confirmed`.
- **Edge cases:** S2-EC26, S2-EC27, S2-EC43, EC-DB02, EC-DB03, EC-DB07, EC-DB09, EC-DB10, EC-DB19 (lệch giờ 45s vẫn trong cửa sổ 300s), EC-AI20
- **DoD:** test chữ ký sai/replay/trùng key; test quorum; test RLS tenant; webhook < 70ms (fake).
- **Verify:** `uv run pytest tests/mule_graph -q`

### T15 — Copilot B2B + hồ sơ SIMO + triage Giờ Vàng + TTS (WP1-09 phần 2)
- **branch:** `task/backend-vayshield/T15`
- **owns:** `backend/app/copilot/**`, `backend/app/triage/**`, `backend/app/api/routes/{ops,triage}.py`, `backend/tests/copilot/**`
- **spec:** File 3 §5.2 (3 tác vụ Copilot, guardrail cấm link/SĐT lạ), File 4 §4.1 (`/ops/mule-clusters`, `/simo-dossier`, SSE live feed, RBAC `FRAUD_ANALYST`; `/triage/golden-hour-dossier` < 120ms), File 2 S2-EC48, team_division WP1-09 (`/tts/synthesize-vi`)
- **Yêu cầu:** API cụm + hồ sơ SIMO (đã làm mờ dữ liệu nạn nhân); advisory writer có guardrail; triage: tra BIN → hotline phong tỏa + PDF trình báo; TTS tiếng Việt (đọc đúng viết tắt, EC-AI26).
- **Edge cases:** EC-AI21, EC-AI26, S2-EC48, EC-OPS15 (JWT judge-ops không hết hạn giữa buổi chấm)
- **DoD:** test guardrail chặn URL/SĐT ngoài whitelist; test RBAC; test dossier không chứa PII.
- **Verify:** `uv run pytest tests/copilot -q`

### T16 — Sandbox Mode backend + VIP quota (WP1-10)
- **branch:** `task/backend-vayshield/T16`
- **owns:** `backend/app/sandbox/**`, `backend/app/api/routes/sandbox.py`, `backend/tests/sandbox/**`
- **spec:** File 4 EC-DB16, File 6 §1.2 mục 3 (VIP lane + budget breaker), File 2 S2-EC28, File 5 §2.1 (`?sandbox=1`)
- **Yêu cầu:** `X-Sandbox-Session` ephemeral → namespace riêng mỗi phiên; `POST /sandbox/simulate-event` (giải ngân, cuộc gọi, mở app ngân hàng, B2B webhook) chạy qua **đúng** lõi thật T05/T12/T14; key pool `GEMINI_VIP_KEYS` cho link giám khảo; cầu chì ngân sách.
- **Edge cases:** EC-DB16, S2-EC28, EC-OPS10 (crawler spam), EC-OPS05
- **DoD:** test cô lập 2 phiên sandbox; test dữ liệu sandbox không lọt vào live; test breaker ngắt khi vượt ngân sách.
- **Verify:** `uv run pytest tests/sandbox -q`

### T17 — Runner benchmark + 3 thí nghiệm ablation (WP1-11 phần 2)
- **branch:** `task/backend-vayshield/T17`
- **owns:** `benchmark-dataset/runner/**`, `benchmark-dataset/results/**`
- **spec:** File 3 §6.1 (metric + SLA), §6.2 (Ablation 1–3), File 6 §3 (slide 8)
- **Yêu cầu:** runner chạy 4 tập → bảng metric (APR MAE, Advance-Fee recall, Sender-QR attribution, Injection robustness, Zero-OTP leak, Coached detection…), 3 ablation, xuất `results/summary.md` + CSV giao cho Nhóm 2.
- **DoD:** chạy tái lập được bằng 1 lệnh; kết quả thật (không bịa số); ghi rõ chỗ chưa đạt SLA.
- **Verify:** `uv run python benchmark-dataset/runner/run_all.py --quick`

### T18 — Hạ tầng cloud zero-cold-start + Docker + gitleaks + k6 (WP1-12)
- **branch:** `task/backend-vayshield/T18`
- **owns:** `infra/**`, `backend/Dockerfile`, `docker-compose.yml`, `.gitleaks.toml`, `loadtest/**`, `.gitattributes`
- **spec:** File 6 §1.1, §1.2 (7 kỹ thuật SRE), §2 hàng 4 (k6 20 phiên), EC-OPS01, 03, 04, 05, 11, 13, 18, 19, 20, 22
- **Yêu cầu:** Dockerfile `linux/amd64`, `.gitattributes` ép LF cho `*.sh`/`*.lua`/`*.sql`; compose prod chạy bằng 1 lệnh; cấu hình Cloud Run/Railway (`min-instances=1`, HTTPS 443, anti-buffering SSE); healthcheck chống ngủ của PG serverless; gitleaks; k6 20 phiên đồng thời.
- **⚠ Deploy thật cần người duyệt** (hook sẽ hỏi). Agent chỉ viết cấu hình + chạy local.
- **DoD:** `docker compose up --build` sạch trên máy trắng; `gitleaks detect` sạch; k6 báo p95 trong SLA.
- **Verify:** `docker compose up --build -d && node .claude/scripts/run-checks.js all backend`

## 6. Thứ tự tích hợp & mốc
| Mốc (team_division §4) | Task phải merged | Bàn giao cho Nhóm 2 |
|---|---|---|
| **M0** — khóa hợp đồng (giờ thứ 2) | T00 | `contracts/*` + mock server |
| **M1** — 40%: Trạm 2 sống | T01, T02, T03, T04, T09, T10, T11 | URL staging `/audits/contract-stream` + `/recalculate` |
| **M2** — 75%: Trạm 3, giải cứu 2 chiều, B2B | T05, T07, T08, T12, T13, T14, T15, T16 | WS `/ws/v1/guardian`, Push, API `/workspace`, Sandbox |
| **M3** — 90%: số liệu khoa học | T06, T17 | bảng ablation cho Slide 8 |
| **M4** — đóng băng | T18 | `.zip` sạch, gitleaks, `docker compose up` |

Trong mỗi wave, merge theo thứ tự ID tăng dần. Sau mỗi lần merge chạy `node .claude/scripts/run-checks.js all backend`.

## 7. Rủi ro & câu hỏi mở
- **R1:** Dữ liệu seed (hotline, packageName, nguyên văn luật) phải kiểm chứng bởi người. Agent không được bịa (T08).
- **R2:** Contract M0 cần Nhóm 2 đồng ý. Đổi contract sau M0 → task riêng có người duyệt.
- **R3:** Quota/chi phí Gemini: test luôn dùng fake. Benchmark T17 gọi thật → cần người cho phép và có ngân sách.
- **R4:** File 2 chỉ có code mẫu cho solver APR. `solve_ear_xirr`/MIRR tự cài theo công thức, đối chiếu với ví dụ số trong spec.
- **Q1:** Nhóm 1 có nhiều người không? Nếu bạn chỉ phụ trách một phần (vd không làm AI/benchmark), đánh dấu `skip` các task đó trong PROGRESS.md.

## 8. Tiến độ
Bảng theo dõi: **`.claude/plans/backend-vayshield/PROGRESS.md`**. Tính % bằng `node .claude/scripts/progress.js backend-vayshield`.
