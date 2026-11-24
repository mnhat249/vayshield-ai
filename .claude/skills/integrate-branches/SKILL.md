---
name: integrate-branches
description: Gộp các branch task đã verify pass + review approve vào base branch theo thứ tự trong plan, chạy lại toàn bộ check sau mỗi merge, dọn worktree. Luôn cần người duyệt trước khi merge/push. Dùng khi nói "merge các task", "tích hợp wave", "/integrate-branches <slug>".
---

# integrate-branches — Ghép nhánh có người duyệt

Args: `$ARGUMENTS` = `<slug>` [ID...].

## Quy trình
1. Lọc task có Verify = `pass` và Review = `approve`. Task khác → liệt kê lý do bị loại.
2. **Hỏi người dùng duyệt** danh sách + thứ tự merge (mục 5 của plan). Không có "OK" rõ ràng → dừng.
3. Đảm bảo main checkout sạch và đang ở base branch.
4. Với từng branch theo thứ tự:
   - `git merge --no-ff task/<slug>/<ID> -m "merge(<slug>): <ID> <tên>"`
   - Xung đột → `git merge --abort`, báo người dùng file xung đột (nghĩa là plan chia `owns` sai → thêm bài học vào CLAUDE.md §10).
   - `node .claude/scripts/run-checks.js all` — FAIL → `git reset --hard HEAD~1` **chỉ khi người dùng đồng ý**, báo lỗi.
   - Cập nhật plan: `merged`.
5. Sau khi merge xong: dọn worktree của task đã merge (`git worktree remove <path>`; branch giữ lại đến khi người dùng bảo xoá).
6. Không `git push` trừ khi người dùng yêu cầu rõ trong lượt này.
7. Nếu còn wave sau → gợi ý `/dispatch-tasks <slug>` cho wave tiếp theo. Hết task → `status: integrated`, gợi ý `/handoff`.
