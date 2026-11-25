# Handoff — backend-vayshield wave 1 — 2026-11-25

## Mục tiêu đang làm
Chạy plan `.claude/plans/backend-vayshield.md` (T00–T18) theo workflow: dispatch → verify → review → merge. Tiến độ: `.claude/plans/backend-vayshield/PROGRESS.md`.

## Quyết định đã chốt
- Repo: https://github.com/mnhat249/vayshield-ai (PUBLIC). Agent worktree được tạo từ `origin/main` → **phải push main** trước khi dispatch wave mới.
- Máy không có Docker/Postgres/Redis: dùng `uv` (~/bin/uv.exe, Python 3.12), fakeredis; test Postgres thật (`@pytest.mark.pg`) chạy trên GitHub Actions qua PR draft.
- 3 số trong đặc tả File 2 sai so với công thức (reviewer xác nhận): XIRR 8,5tr→12tr/30 ngày = 6539,1% (spec 6568,5%); S2-EC12 = 333,2% (spec 351,8%); §1.3 ≈ 100% (spec ~96,5%). Test theo công thức. Sửa `momo/*.md` cần người duyệt.

## Trạng thái task
| Task | Branch commit | Trạng thái | Việc còn lại |
|---|---|---|---|
| T00 | merged | ✅ | `.env.example` chưa tạo (deny rule `Read(./.env.*)` chặn — người dùng tự tạo hoặc thêm ngoại lệ) |
| T07 | merged | ✅ | – |
| T01 | d58df8e (pushed, PR #1 draft) | đã sửa review | xem CI PR #1 xanh → merge |
| T02 | db6acc2 + 2 file chưa commit | đang sửa review (dừng giữa chừng) | resume agent: 1 major (điểm amount=0 trong `_validate`), 3 minor, nit |
| T03 | 98c25f0 + 5 file chưa commit | đang sửa review (dừng) | resume: 1 blocker (detect_qr_codes return sớm), 4 major |
| T04 | 66f4691 + 5 file chưa commit | đang implement (dừng khi viết test) | resume → verify → review |
| T05 | 8402eb1 + 10 file chưa commit | implement gần xong (test pass, chưa lint/commit) | resume → verify → review |
| T06 | 2bd9556 + 3 file chưa commit | đang sửa nhãn (dừng) | resume: nhãn 9 mã V_*, APR_VN_365, góp ngày |
| T08–T18 | – | pending | sau khi wave 1 merged |

Worktree: `.claude/worktrees/agent-*` (mỗi cái một branch `task/backend-vayshield/Txx`). Không xoá — còn việc chưa commit.

## Việc tiếp theo (theo thứ tự)
1. Resume (hoặc dispatch lại implementer trên đúng worktree) T02, T03, T04, T05, T06 — mỗi agent tự `git status` rồi làm tiếp.
2. `gh run list -R mnhat249/vayshield-ai --branch task/backend-vayshield/T01 -L 1` → xanh thì merge T01.
3. Verify + review T04, T05; re-verify T02/T03/T06 sau sửa → merge từng cái, push main.
4. Wave 2: T08, T09, T10 (prompts có sẵn trong `.claude/plans/backend-vayshield/prompts/`).
5. Lưu ý quota: 7 agent song song làm chạm giới hạn 5h — nên chạy ≤ 3–4 agent/lượt.

## Câu hỏi đang chờ người dùng
- Có sửa 3 con số sai trong `momo/session_02_*.md` không?
- `.env.example`: tạo tay hay cho phép ngoại lệ trong `.claude/settings.json`?
- Tiếp tục chạy với bao nhiêu agent song song?
