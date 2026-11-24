---
slug: <kebab-case-slug>
title: <Tên tính năng / mốc>
status: draft            # draft → approved → dispatched → reviewed → integrated
base_branch: main
created: <YYYY-MM-DD>
spec_refs: [<File 2 §4.1>, <File 4 §4.2(A)>]
work_packages: [<WP1-03>, <WP2-02>]
---

# Plan: <title>

## 1. Mục tiêu & phạm vi
- Mục tiêu: ...
- Ngoài phạm vi (KHÔNG làm): ...

## 2. Quyết định đã chốt
- D1: ... (lý do)

## 3. Hợp đồng chung (phải có TRƯỚC khi chạy song song)
- File: `contracts/...` — trạng thái: có sẵn | tạo ở T00 (wave 0, chạy một mình)
- Tên field / endpoint / event mà các task phải tuân theo: ...

## 4. Danh sách task

| ID | Tên | Wave | Phụ thuộc | Vùng sở hữu (owns) | Agent | Model |
|----|-----|------|-----------|--------------------|-------|-------|
| T01 | ... | 1 | – | `backend/app/finance/**` | implementer | sonnet |
| T02 | ... | 1 | – | `web-portal/src/app/(b2c)/**` | implementer | sonnet |
| T03 | ... | 2 | T01 | `backend/app/api/audits.py`, `backend/tests/api/test_audits.py` | implementer | sonnet |

> Luật: các task cùng wave có `owns` KHÔNG giao nhau (kể cả file cấu hình chung như `package.json`, `pyproject.toml`, `docker-compose.yml`, lockfile).
> File chung phải giao cho đúng 1 task, hoặc tách ra task wave 0.

### T01 — <tên>
- **branch:** `task/<slug>/T01`
- **owns:** `backend/app/finance/**`, `backend/tests/finance/**`
- **reads (chỉ đọc):** `contracts/openapi.yaml`, `momo/session_02_*.md` §4.1–4.4
- **spec:** File 2 §4.1 → 4.4; edge cases: `EC-xx`, ...
- **Yêu cầu:** (gạch đầu dòng cụ thể, kiểm chứng được)
- **Definition of Done:**
  - [ ] ...
  - [ ] Test cho từng edge case nêu trên
  - [ ] `node .claude/scripts/run-checks.js all backend` PASS
- **Verify:** lệnh cụ thể để chứng minh xong
- **Rủi ro / lưu ý:** ...

### T02 — ...

## 5. Thứ tự tích hợp
1. Merge wave 1 theo thứ tự: T01 → T02 (lý do)
2. Sau mỗi merge: `node .claude/scripts/run-checks.js all`

## 6. Trạng thái thực thi (orchestrator cập nhật)
| ID | Branch | Trạng thái | Verify | Review | Ghi chú |
|----|--------|-----------|--------|--------|---------|
| T01 | task/<slug>/T01 | pending | – | – | |
