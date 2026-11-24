# Workflow team agent — VayShield AI

```
 Bạn (người duyệt)
   │  "/orchestrate WP1-03 lõi tài chính APR/XIRR"
   ▼
 ┌──────────────────────── Session chính (Opus = tech lead) ────────────────────────┐
 │ 1. /plan-feature      → .claude/plans/<slug>.md  (task, owns, wave, DoD)          │
 │                        🧑 duyệt plan  → commit plan                                │
 │ 2. /write-task-prompts → .claude/plans/<slug>/prompts/T01.md, T02.md ...          │
 │                        (template + nguyên tắc skill anthropic-prompt-engineer)    │
 │ 3. /dispatch-tasks    → song song, mỗi task 1 agent `implementer`:                │
 │        T01 ─ worktree A ─ branch task/<slug>/T01                                  │
 │        T02 ─ worktree B ─ branch task/<slug>/T02                                  │
 │        T03 ─ worktree C ─ branch task/<slug>/T03                                  │
 │ 4. /verify            → agent `test-runner` chạy check thật trên từng branch      │
 │ 5. /review-branch     → agent `reviewer` (Opus, context sạch) review từng branch  │
 │                        🧑 duyệt                                                   │
 │ 6. /integrate-branches → merge --no-ff vào main, chạy lại toàn bộ check           │
 │ 7. wave tiếp theo … → docs-writer → /handoff                                      │
 └───────────────────────────────────────────────────────────────────────────────────┘
```

## Vai trò
| Vai | Ở đâu | Model | Quyền |
|---|---|---|---|
| Tech lead / orchestrator | session chính | Opus | điều phối, không viết code sản phẩm |
| `planner` | `.claude/agents/planner.md` | opus | chỉ đọc |
| `implementer` | `.claude/agents/implementer.md` | sonnet (đổi sang opus cho task khó trong plan) | sửa file trong `owns`, commit trên branch riêng |
| `test-runner` | `.claude/agents/test-runner.md` | sonnet | chạy lệnh, không sửa code |
| `reviewer` | `.claude/agents/reviewer.md` | opus | chỉ đọc |
| `docs-writer` | `.claude/agents/docs-writer.md` | sonnet | chỉ sửa tài liệu |

## Skills
**Workflow (mới):** `orchestrate`, `plan-feature`, `write-task-prompts`, `dispatch-tasks`, `verify`, `review-branch`, `integrate-branches`, `handoff`.
**Của bạn (đã chuyển từ `skills/SKILLS/` lên `skills/` để Claude nhận diện):**
`anthropic-architect`, `anthropic-prompt-engineer` (dùng trong write-task-prompts), `design-brief-generator`,
`engineer-expertise-extractor`, `engineer-skill-creator`, `qa-test-planner` (dùng trong verify), `technical-launch-planner`,
`backend-architect`, `backend-to-frontend-handoff-docs` (dùng bởi docs-writer), `code-reviewer` (dùng bởi reviewer),
`content-research-writer`, `database-migration`, `senior-backend`.

> Thêm skill mới: tạo `.claude/skills/<tên>/SKILL.md` (đúng 1 cấp, không lồng thư mục con) với frontmatter `name` + `description`.

## Commands (việc nhanh)
`/test [stack]` · `/lint [stack]` · `/status` · `/worktrees`

## Hooks (`.claude/settings.json`)
| Hook | Khi nào | Làm gì |
|---|---|---|
| `guard-bash.js` | trước mọi lệnh Bash | **chặn**: force-push main, `rm -rf /`, DROP DATABASE, `git add -A/.`, đọc `.env`, commit file/chuỗi secret. **Hỏi người**: push, deploy (vercel/gcloud/railway/docker push…), gh pr merge, request POST/DELETE ra ngoài, DELETE FROM, gửi mail/SMS, reset --hard |
| `protect-files.js` | trước Edit/Write | **chặn** ghi `.env`/khóa/keystore và nội dung giống API key. **Hỏi người** khi sửa `contracts/`, `momo/*.md`, settings/hooks |
| `format-on-edit.js` | sau Edit/Write | `ruff format`+`ruff check --fix` (.py), prettier/eslint (nếu đã cài trong web-portal), ktlint (.kt). Còn lỗi → báo lại cho Claude tự sửa |

## Luật chống xung đột khi chạy song song
1. Task cùng wave có `owns` không giao nhau — kể cả lockfile, `package.json`, `pyproject.toml`, router tổng.
2. Hợp đồng chung (`contracts/`) có trước wave 1 (mốc M0 trong team_division_2_groups.md).
3. Plan + prompts phải được **commit** trước khi dispatch (worktree tạo từ HEAD).
4. `.claude/worktrees/` nằm trong `.gitignore` — không bao giờ `git add -A` ở root.

## Ví dụ nhanh
```
/orchestrate WP1-03 Deterministic Financial & Interval Kernel
/plan-feature M0: khóa contracts/openapi.yaml + mock server
/dispatch-tasks wp1-03-financial-kernel wave=1 max=3
/review-branch task/wp1-03-financial-kernel/T02
/handoff
```
