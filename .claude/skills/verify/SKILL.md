---
name: verify
description: Kiểm chứng một task/branch bằng bằng chứng thật — chạy test/lint/typecheck trên đúng branch, đối chiếu DoD, liệt kê case chưa kiểm. Dùng sau khi agent báo xong, hoặc khi người dùng hỏi "đã test chưa", "/verify <slug> [ID]" hoặc "/verify" cho thay đổi hiện tại.
---

# verify — "Xong rồi" không phải bằng chứng

Args: `$ARGUMENTS` = `<slug>` [ID...] — hoặc trống để verify thay đổi trong working tree hiện tại.

## Quy trình (cho mỗi task)
1. Gọi agent `test-runner` (không cần worktree mới — nó dùng `git worktree add` tạm hoặc worktree sẵn có của branch, read-only với code) với prompt gồm: branch, DoD + lệnh verify trong plan, danh sách edge case.
   Có thể gọi nhiều `test-runner` song song trong 1 message cho nhiều task.
2. test-runner phải trả về:
   - Lệnh đã chạy + exit code + số test pass/fail (trích output thật).
   - DoD: từng mục ✅/❌ kèm bằng chứng.
   - Edge case trong plan không có test tương ứng.
   - Với UI: có screenshot desktop + mobile không (nếu task là web, dùng skill `run`/browser pane để xem).
3. Ghi kết quả vào cột Verify của plan: `pass` / `fail(<lý do ngắn>)`.
4. Fail → gửi lại cho agent cũ (SendMessage tới agent id nếu còn) hoặc dispatch lại task với phần "Lỗi cần sửa".
5. Có thể dùng skill `qa-test-planner` để sinh thêm test case khi phát hiện vùng chưa phủ.
