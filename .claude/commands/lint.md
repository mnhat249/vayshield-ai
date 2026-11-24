---
description: Chạy lint + typecheck (backend | web | android | all)
argument-hint: "[backend|web|android|all]"
allowed-tools: Bash(node .claude/scripts/run-checks.js:*)
---
Chạy lần lượt `node .claude/scripts/run-checks.js lint $ARGUMENTS` và `node .claude/scripts/run-checks.js typecheck $ARGUMENTS`. Báo bảng tóm tắt và các lỗi chính (file:line). Không tự sửa trừ khi được yêu cầu.
