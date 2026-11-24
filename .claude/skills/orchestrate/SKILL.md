---
name: orchestrate
description: Chạy trọn workflow team agent từ yêu cầu đến code đã tích hợp - plan (Opus) → duyệt → sinh prompt → dispatch song song nhiều worktree/branch → verify → review độc lập → duyệt → merge → handoff. Dùng khi người dùng nói "làm WP..., chạy cả quy trình", "orchestrate", "/orchestrate <mô tả>".
---

# orchestrate — Nhạc trưởng (session chính, nên chạy bằng Opus)

Args: `$ARGUMENTS` = mô tả tính năng / mã WP / slug plan có sẵn.

Session chính đóng vai **tech lead**: lập kế hoạch, giao việc, kiểm tra, KHÔNG tự viết code sản phẩm.

## Các bước & cổng duyệt của người (🧑)
1. `/plan-feature $ARGUMENTS` → 🧑 duyệt plan.
2. Commit plan: `git add .claude/plans/<slug>.md && git commit -m "plan(<slug>): ..."` (worktree tạo từ HEAD nên plan phải được commit).
3. `/write-task-prompts <slug>` → commit thư mục prompts.
4. Lặp theo wave:
   1. `/dispatch-tasks <slug>` — các agent `implementer` chạy song song, mỗi agent 1 worktree + branch `task/<slug>/<ID>`.
   2. `/verify <slug>` — agent `test-runner` chạy check thật trên từng branch.
   3. `/review-branch <slug>` — agent `reviewer` (Opus, context sạch) review từng branch.
   4. Sửa vòng lặp tối đa 2 lần/task; quá 2 lần → 🧑 quyết định.
   5. 🧑 duyệt → `/integrate-branches <slug>`.
5. Khi plan `integrated`: gọi agent `docs-writer` cập nhật README/CHANGELOG/handoff docs API nếu có thay đổi API (skill `backend-to-frontend-handoff-docs`).
6. `/handoff` nếu phiên dài.

## Quy tắc
- Mỗi bước kết thúc bằng bảng trạng thái ngắn cho người dùng (task, branch, verify, review).
- Deploy / push / gửi tin / xoá dữ liệu / đụng tiền: không bao giờ tự làm — chỉ đề xuất lệnh.
- Báo cáo cuối theo CLAUDE.md §8.
