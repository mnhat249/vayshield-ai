---
name: write-task-prompts
description: Bước 2 của workflow team agent. Từ plan đã duyệt (.claude/plans/<slug>.md), sinh prompt chuẩn hoá cho từng task theo template + nguyên tắc của skill anthropic-prompt-engineer, lưu ở .claude/plans/<slug>/prompts/<ID>.md. Dùng khi nói "tạo prompt cho các task", "/write-task-prompts <slug>".
---

# write-task-prompts — Biến mỗi task trong plan thành prompt tự đủ cho agent

Args: `$ARGUMENTS` = `<slug>` [danh sách ID, mặc định: mọi task `pending`].

## Quy trình
1. Đọc `.claude/plans/<slug>.md`. Nếu `status` không phải `approved`/`dispatched` → dừng, nhắc người dùng duyệt plan trước.
2. Đọc nguyên tắc viết prompt: `.claude/skills/anthropic-prompt-engineer/SKILL.md` và
   `.claude/skills/anthropic-prompt-engineer/references/claude_4_best_practices.md` (chỉ lần đầu trong phiên).
3. Với mỗi task, điền `.claude/templates/task-prompt.md`:
   - `role`: backend Python / frontend Next.js / Android Kotlin / data-AI / devops — theo `owns`.
   - `why_this_task_matters`: 2–4 câu lấy từ đặc tả, nói người dùng cuối hưởng lợi gì.
   - `spec_refs`: đường dẫn file + số mục chính xác (vd `momo/session_02_system_architecture_and_financial_engine.md` §4.2). **Mở file kiểm tra mục đó tồn tại.**
   - `requirements`: cụ thể, đo được (tên hàm, endpoint, field JSON đúng như `contracts/`, ngưỡng hiệu năng nếu spec có).
   - `edge cases`: liệt kê mã + 1 dòng mô tả lấy từ đặc tả.
   - `verify`: lệnh thật (`node .claude/scripts/run-checks.js ...`, `pytest path::test`), không ghi "chạy test" chung chung.
   - Prompt phải **tự đủ**: agent không thấy cuộc hội thoại này. Không dùng "như đã bàn", "theo plan ở trên".
4. Lưu `.claude/plans/<slug>/prompts/<ID>.md`.
5. Tự rà mỗi prompt theo checklist (sửa nếu trượt):
   - [ ] Có `owns` + câu cấm sửa ngoài vùng
   - [ ] Có branch đúng `task/<slug>/<ID>`
   - [ ] Mọi yêu cầu kiểm chứng được
   - [ ] Có lệnh verify thật
   - [ ] Có report_format
   - [ ] Không chứa secret / dữ liệu cá nhân
6. Báo lại: danh sách file prompt đã tạo + 1 dòng tóm tắt mỗi task. Gợi ý bước tiếp: `/dispatch-tasks <slug>`.
