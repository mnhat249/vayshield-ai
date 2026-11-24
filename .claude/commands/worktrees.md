---
description: Liệt kê worktree/branch task và số commit, file thay đổi của mỗi cái
allowed-tools: Bash(git worktree list:*), Bash(git branch:*), Bash(git log:*), Bash(git diff:*)
---
Chạy `git worktree list` và `git branch --list "task/*"`. Với mỗi branch `task/*`: `git log --oneline main..<branch> | wc -l` và `git diff --stat main...<branch> | tail -1`.
Trình bày bảng: branch | worktree path | số commit | file/dòng thay đổi. Chỉ ra worktree mồ côi (branch đã merge vào main) và gợi ý lệnh dọn — KHÔNG tự xoá.
