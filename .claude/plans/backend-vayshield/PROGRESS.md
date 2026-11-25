# PROGRESS — Backend VayShield AI

Plan: [`../backend-vayshield.md`](../backend-vayshield.md) · Cập nhật %: `node .claude/scripts/progress.js backend-vayshield --write`

<!-- progress:start -->
**Tổng tiến độ: 30.6%** `[██████░░░░░░░░░░░░░░]` — merged 2/19 task · cập nhật 2026-11-25

| Wave | Tiến độ | | Mốc | Tiến độ |
|---|---|---|---|---|
| W0 | 100% | | M0 | 100% |
| W1 | 53% | | M1 | 42% |
| W2 | 0% | | M2 | 15% |
| W3 | 0% | | M3 | 25% |
| W4 | 0% | | M4 | 0% |
| W5 | 0% | |  |  |
<!-- progress:end -->

## Trạng thái & cách tính
| Trạng thái | Nghĩa | Tính điểm |
|---|---|---|
| `pending` | chưa bắt đầu | 0% |
| `running` | agent đang làm | 20% |
| `implemented` | agent đã commit trên branch | 50% |
| `verified` | `/verify` pass | 70% |
| `reviewed` | `/review-branch` approve | 85% |
| `merged` | đã vào `main` | 100% |
| `blocked` | chờ người quyết định | giữ nguyên điểm trước đó (ghi lý do) |
| `skip` | không thuộc phần việc của tôi | loại khỏi mẫu số |

**Trọng số** lấy từ tỷ trọng WP trong `team_division_2_groups.md` §2, quy về thang 100% của backend.

## Bảng task
| ID | WP | Task | Wave | Mốc | Phụ thuộc | Trọng số | Trạng thái | Branch | Verify | Review | Cập nhật | Ghi chú |
|----|----|------|------|-----|-----------|---------:|-----------|--------|--------|--------|----------|---------|
| T00 | WP1-06 | Khung FastAPI + contracts M0 | 0 | M0 | – | 2 | merged | task/backend-vayshield/T00 | pass (95✓ 1 skip pg) | approve (sau 1 vòng sửa) | 2026-11-25 | Cần Nhóm 2 duyệt contract |
| T01 | WP1-01 | PostgreSQL schema + RLS + hash-chain | 1 | M1 | T00 | 10 | verified | task/backend-vayshield/T01 | pass local + CI pg (PR#1) | changes (2 major) | – | |
| T02 | WP1-03 | Kernel tài chính APR/XIRR/khoảng/MIRR | 1 | M1 | T00 | 8 | verified | task/backend-vayshield/T02 | pass (177✓) | – | – | ≥35 pytest |
| T03 | WP1-04 | Thị giác đối kháng + VietQR | 1 | M1 | T00 | 10 | verified | task/backend-vayshield/T03 | pass (196✓) | – | – | |
| T04 | WP1-05 | Trích xuất LLM + Conformal + Voice | 1 | M1 | T00 | 12 | running | task/backend-vayshield/T04 | – | – | – | |
| T05 | WP1-07 | Redis HSM + Lua smurf + idempotency | 1 | M2 | T00 | 8 | running | task/backend-vayshield/T05 | – | – | – | |
| T06 | WP1-11 | Dataset Bench-340 (data + nhãn) | 1 | M3 | T00 | 2 | implemented | task/backend-vayshield/T06 | validate pass | changes (nhãn lệch spec) | – | |
| T07 | WP1-08 | Ontology 10 trục C1–C10 | 1 | M2 | T00 | 4 | merged | task/backend-vayshield/T07 | pass (188✓) | approve (sau 1 vòng sửa) | – | |
| T08 | WP1-02 | Seed registry + luật + mule mẫu | 2 | M2 | T01 | 6 | pending | task/backend-vayshield/T08 | – | – | – | Người xác minh dữ liệu thật |
| T09 | WP1-03 | Ma trận 9 phán quyết + lookalike | 2 | M1 | T02,T01 | 4 | pending | task/backend-vayshield/T09 | – | – | – | |
| T10 | WP1-06 | Mật mã & bảo mật nền | 2 | M1 | T01 | 2 | pending | task/backend-vayshield/T10 | – | – | – | |
| T11 | WP1-06 | API Trạm 1 & 2 (SSE) | 3 | M1 | T02,T03,T04,T09,T10 | 6 | pending | task/backend-vayshield/T11 | – | – | – | |
| T12 | WP1-08 | API Trạm 3 + WS + leo thang + gia đình | 3 | M2 | T05,T07,T10 | 6 | pending | task/backend-vayshield/T12 | – | – | – | |
| T13 | WP1-02 | Legal GraphRAG | 3 | M2 | T08 | 2 | pending | task/backend-vayshield/T13 | – | – | – | |
| T14 | WP1-09 | Mule graph + Dynamic VA + B2B webhook | 3 | M2 | T01,T10 | 5 | pending | task/backend-vayshield/T14 | – | – | – | |
| T15 | WP1-09 | Copilot + SIMO + triage + TTS | 4 | M2 | T13,T14 | 3 | pending | task/backend-vayshield/T15 | – | – | – | |
| T16 | WP1-10 | Sandbox Mode + VIP quota | 4 | M2 | T11,T12 | 4 | pending | task/backend-vayshield/T16 | – | – | – | |
| T17 | WP1-11 | Runner benchmark + ablation | 4 | M3 | T06,T11,T12 | 2 | pending | task/backend-vayshield/T17 | – | – | – | Gọi Gemini thật: cần duyệt |
| T18 | WP1-12 | Cloud + Docker + gitleaks + k6 | 5 | M4 | tất cả | 4 | pending | task/backend-vayshield/T18 | – | – | – | Deploy cần duyệt |

## Nhật ký
| Ngày | Sự kiện |
|---|---|
| 2026-11-25 | Tạo plan + bảng progress (status: draft, chờ duyệt) |
| 2026-11-25 | Plan approved; T00 dispatched (wave 0) |
| 2026-11-25 | T00 implemented (PARTIAL: .env.example bị chặn quyền, chưa chạy Docker) → verify pass |
| 2026-11-25 | T00 review: 4 major + 9 minor → sửa xong (a0ef22f) → merged. Mốc M0 xong (contract chờ Nhóm 2 xem) |
| 2026-11-25 | Wave 1 dispatched: T01–T07 song song (7 worktree) |
| 2026-11-25 | Giới hạn API 5h làm dừng T02–T06 + review T01 → đã resume. T07 review: 9 major (báo động giả do đồng âm không dấu) → đang sửa |
| 2026-11-25 | T02/T03 verified. T01 CI pg xanh, review 2 major → đang sửa. T06 nhãn dùng BLOCK/DANGER + XIRR thay vì 9 mã V_* + APR_VN_365 → đang sửa. T02 báo 3 số trong spec không khớp công thức (chờ xác nhận) |
| 2026-11-25 | T07 merged (approve (sau 1 vòng sửa)) |
