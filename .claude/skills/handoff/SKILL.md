---
name: handoff
description: Tóm tắt context để bàn giao sang session mới như một team thật — mục tiêu, quyết định đã chốt, file đã sửa, test đã chạy, lỗi còn tồn, việc tiếp theo. Dùng khi hội thoại dài, trước khi /clear, hoặc khi nói "bàn giao", "tóm tắt để làm tiếp", "/handoff".
---

# handoff — Giữ context sạch

## Quy trình
1. Thu thập sự thật (không dựa trí nhớ):
   - `git branch --show-current`, `git status --short`, `git log --oneline -15`, `git worktree list`
   - Plan đang chạy: `.claude/plans/*.md` có `status` khác `integrated` → đọc bảng mục 6.
2. Ghi file `.claude/handoff/<YYYY-MM-DD>-<slug-hoặc-chủ-đề>.md`:

```markdown
# Handoff — <chủ đề> — <ngày>
## Mục tiêu đang làm
## Quyết định đã chốt (kèm lý do)
## Trạng thái plan / task   (copy bảng mục 6)
## File đã sửa (theo branch)
## Test đã chạy + kết quả thật
## Lỗi / rủi ro còn tồn
## Việc tiếp theo (theo thứ tự, lệnh cụ thể)
## Câu hỏi đang chờ người dùng
```
3. Nếu trong phiên có bài học (Claude làm sai, người dùng sửa) → đề xuất thêm dòng vào `CLAUDE.md §10`.
4. In ra đường dẫn file. Session mới chỉ cần: "đọc `.claude/handoff/<file>` rồi làm tiếp".
