---
description: Chạy test nhanh (backend | web | android | all)
argument-hint: "[backend|web|android|all]"
allowed-tools: Bash(node .claude/scripts/run-checks.js:*)
---
Chạy `node .claude/scripts/run-checks.js test $ARGUMENTS` (trống = all). Báo lại bảng TÓM TẮT; nếu FAIL, trích các dòng lỗi chính và đoán nguyên nhân trong 1-2 câu. Không tự sửa code trừ khi được yêu cầu.
