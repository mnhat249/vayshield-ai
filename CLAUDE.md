# CLAUDE.md — Sổ tay làm việc của dự án VayShield AI

> File này được nạp vào mọi session. Giữ ngắn, đúng, cập nhật liên tục.
> **Claude làm sai ở đâu → thêm 1 dòng vào mục "Bài học đã rút ra" ở cuối file**, đừng chỉ sửa kết quả.

## 1. Mục tiêu sản phẩm
VayShield AI — sản phẩm thật chạy trực tuyến (B2C Web/PWA + B2B Fintech SaaS + Android Native) kèm `Sandbox Mode` kiểu Stripe,
bảo vệ người đi vay khỏi app vay lừa đảo / lãi cắt cổ / chiếm đoạt OTP.
Nguồn sự thật (đọc đúng mục trước khi code, KHÔNG bịa yêu cầu):

| Tài liệu | Nội dung |
| --- | --- |
| `momo/master_roadmap.md` | Chỉ mục tổng |
| `momo/session_01_*.md` (File 1) | Chiến lược, pháp lý, `EC-S01..20` |
| `momo/session_02_*.md` (File 2) | Kiến trúc, lõi tài chính APR/XIRR, HSM, 48 edge cases |
| `momo/session_03_*.md` (File 3) | AI Vision/Voice, ontology 10 trục, `EC-AI01..26` |
| `momo/session_04_*.md` (File 4) | DB/API/bảo mật, SSE, RLS, `EC-DB01..26` |
| `momo/session_05_*.md` (File 5) | Android + Web UI, `EC-UX01..26` |
| `momo/session_06_*.md` (File 6) | DevOps, QA, pitch, `EC-OPS01..24` |
| `momo/team_division_2_groups.md` | 24 work package `WP1-xx` / `WP2-xx`, ranh giới thư mục |

## 2. Cấu trúc monorepo (theo team_division_2_groups.md)
```
contracts/        # openapi.yaml, sse-events.schema.json, ws-protocol.json — KHÓA sau mốc M0
backend/          # FastAPI (Python 3.12) — Nhóm 1
  ai_pipeline/    # Vision, VietQR, ontology, GraphRAG
  migrations/     # PostgreSQL 16 + pgvector + RLS + hash-chain
benchmark-dataset/
infra/  docker-compose.yml
web-portal/       # Next.js 15 + shadcn/ui — Nhóm 2
android-app/      # Kotlin + Compose Material 3 — Nhóm 2
submission-docs/  # slide, video, A4
.claude/          # agents, skills, commands, hooks, plans, handoff
```
Mỗi task chỉ được sửa file trong vùng `owns` của nó (xem plan). Hai task song song KHÔNG bao giờ chạm cùng file.

## 3. Lệnh chạy (tự phát hiện stack, bỏ qua phần chưa tồn tại)
```bash
node .claude/scripts/run-checks.js test       [backend|web|android|all]
node .claude/scripts/run-checks.js lint       [...]
node .claude/scripts/run-checks.js typecheck  [...]
node .claude/scripts/run-checks.js all        # lint + typecheck + test
```
Chi tiết từng stack:
- backend: `cd backend && uv run pytest -q`, `uv run ruff check .`, `uv run ruff format .`, `uv run mypy app ai_pipeline`
- web-portal: `cd web-portal && npm test`, `npm run lint`, `npx tsc --noEmit`, `npm run build`
- android-app: `cd android-app && ./gradlew testDebugUnitTest lint`

## 4. Quy tắc viết code
- Python: type hints đầy đủ, Pydantic v2 cho schema, hàm tài chính thuần (không I/O) + có unit test; tiền tệ dùng `Decimal`/int đồng, KHÔNG `float`.
- TypeScript: `strict`, type sinh từ `contracts/openapi.yaml`, không `any`; UI dùng component có sẵn (shadcn, Recharts, @xyflow/react) — không tự vẽ CSS/SVG thủ công (File 5).
- Kotlin: Clean Architecture, Compose Material 3, không Canvas tự vẽ.
- Tên API/JSON field phải khớp `contracts/` từng ký tự. Lệch hợp đồng = lỗi chặn merge.
- Mỗi thay đổi logic đi kèm test. Mỗi edge case trong spec mà task chạm tới → có test mang mã `EC-xxx` trong tên/docstring.
- Commit nhỏ, message dạng `feat(backend): ... [T03]`, `fix(web): ...`.

## 5. Vùng KHÔNG được tự sửa (phải hỏi người)
- `contracts/**` sau khi đã khóa M0 — đổi hợp đồng = tạo task riêng, người duyệt.
- `momo/*.md` (tài liệu đặc tả gốc) — chỉ đọc.
- `backend/migrations/` đã apply — chỉ thêm migration mới, không sửa file cũ.
- `.claude/settings.json`, `.claude/hooks/**` — không tự nới lỏng quyền/hook.
- `.env*`, khóa, keystore, `google-services.json` — không đọc, không ghi, không commit.

## 6. Bảo mật
- Không hard-code secret. Mọi key qua biến môi trường; chỉ commit `.env.example` với giá trị giả.
- RLS bật cho mọi bảng đa tenant; webhook B2B xác thực `HMAC-SHA256`; log không chứa OTP/PII.
- Trước khi giao nộp: `gitleaks detect` sạch.
- Việc **deploy, push, gửi tin nhắn/email, xoá dữ liệu, đụng tiền thật** → luôn dừng lại cho người duyệt (hook `guard-bash.js` sẽ hỏi).

## 7. Workflow team agent (chi tiết: `.claude/WORKFLOW.md`)
```
/plan-feature  (Opus lập plan, chia task + vùng file + wave)   ── người duyệt plan
/write-task-prompts  (sinh prompt chuẩn cho từng task)
/dispatch-tasks  (mỗi task → 1 agent `implementer` trong git worktree + branch riêng, chạy song song theo wave)
/verify  +  /review-branch  (agent `test-runner` + `reviewer` độc lập)   ── người duyệt kết quả
/integrate-branches  (merge vào main, chạy lại toàn bộ check)            ── người duyệt merge/push
/handoff  (tóm tắt context trước khi đổi session)
```
Lệnh nhanh: `/test`, `/lint`, `/status`, `/worktrees`.

**Bảng tiến độ:** nếu plan có `.claude/plans/<slug>/PROGRESS.md` thì mọi skill (dispatch/verify/review/integrate) cập nhật trạng thái task **ở file đó** (cột Trạng thái/Verify/Review/Cập nhật + 1 dòng Nhật ký), rồi chạy `node .claude/scripts/progress.js <slug> --write`.
Plan đang chạy: **`backend-vayshield`** (toàn bộ việc backend, 19 task T00–T18).

## 8. Báo cáo sau khi xong việc (bắt buộc, không chấp nhận "xong rồi")
```
## Kết quả
- File đã sửa: ...
- Lệnh đã chạy + kết quả thật (pass/fail, số test): ...
- Edge case đã cover (mã EC-xxx): ...
- Case CHƯA kiểm / rủi ro còn lại: ...
- UI: đã xem desktop/mobile? screenshot/link preview: ...
- Việc tiếp theo: ...
```

## 9. MCP / tool
Chỉ kết nối tool thực sự cần. Hiện tại: không có MCP nào bắt buộc. Khi cần: GitHub (PR/review), Postgres read-only (debug DB).
Không cấp quyền ghi production cho MCP.

## 10. Bài học đã rút ra (thêm vào mỗi khi Claude làm sai)
- Máy dev không có Docker/Postgres/Redis: dùng `uv` + fakeredis; test Postgres thật chạy trên GitHub Actions (`@pytest.mark.pg`).
