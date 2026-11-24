---
description: Tình trạng repo + các plan/task đang chạy
allowed-tools: Bash(git status:*), Bash(git branch:*), Bash(git log:*), Bash(git worktree list:*), Read, Glob
---
Thu thập: `git branch --show-current`, `git status --short`, `git log --oneline -5`, `git worktree list`, `git branch --list "task/*"`.
Đọc mọi `.claude/plans/*.md` có `status` khác `integrated` và lấy bảng mục 6.
Trả lời gọn: branch hiện tại, thay đổi chưa commit, các worktree/branch task, bảng trạng thái từng plan, bước tiếp theo nên làm (1 lệnh).
