---
name: dispatch-tasks
description: Bước 3 của workflow team agent. Gửi prompt của từng task cho một agent implementer riêng, mỗi agent chạy trong git worktree + branch riêng (task/<slug>/<ID>), các task cùng wave chạy SONG SONG. Thu báo cáo, cập nhật bảng trạng thái trong plan. Dùng khi nói "chạy plan", "giao task cho agent", "/dispatch-tasks <slug> [wave]".
---

# dispatch-tasks — Chạy song song nhiều agent trên nhiều branch

Args: `$ARGUMENTS` = `<slug>` [`wave=<n>`] [`only=T01,T03`] [`max=4`].

## Điều kiện trước
- Repo git có ít nhất 1 commit trên `main` và working tree sạch với các file plan/prompt đã commit
  (`git status --porcelain` rỗng). Nếu chưa → dừng, đề nghị người dùng commit trước (worktree tạo từ HEAD, agent sẽ không thấy file chưa commit).
- Plan `status: approved` (hoặc `dispatched` khi chạy wave tiếp theo).
- Đã có `.claude/plans/<slug>/prompts/<ID>.md` cho mọi task sắp chạy; thiếu → chạy `/write-task-prompts` trước.
- Mọi task phụ thuộc (`depends_on`) của wave này đã `merged` vào base branch.

## Quy trình
1. Chọn wave nhỏ nhất còn task `pending` (hoặc wave chỉ định). Kiểm tra lại: `owns` các task trong wave không giao nhau. Giao nhau → dừng và báo.
2. **Song song:** trong MỘT message, gọi công cụ `Agent` một lần cho mỗi task (tối đa `max`, mặc định 4; dư thì chạy lượt sau):
   - `subagent_type`: `implementer` (hoặc agent ghi trong plan)
   - `isolation`: `"worktree"`  ← mỗi agent có worktree riêng, không đè file nhau
   - `model`: theo cột Model trong plan (`sonnet`/`opus`)
   - `description`: `<ID> <tên ngắn>`
   - `prompt`: NGUYÊN VĂN nội dung `.claude/plans/<slug>/prompts/<ID>.md`
3. Cập nhật bảng mục 6 của plan: `running`. Đổi `status: dispatched`.
4. Khi từng agent xong, đọc báo cáo của nó (không tin lời "xong rồi"):
   - Kiểm tra branch tồn tại và có commit: `git log --oneline main..task/<slug>/<ID>`.
   - Kiểm tra không sửa ngoài vùng: `git diff --name-only main...task/<slug>/<ID>` ⊆ `owns`. Lệch → đánh dấu `scope-violation`.
   - Ghi vào bảng: `implemented` / `partial` / `blocked` + ghi chú.
5. Task `blocked`/`partial`: tổng hợp "Cần quyết định" gửi người dùng; KHÔNG tự đoán.
6. Kết thúc wave: chạy `/verify <slug>` rồi `/review-branch <slug>` cho các task `implemented`.

## Không làm
- Không merge, không push, không xoá worktree/branch ở bước này.
- Không sửa code thay agent trong main checkout.
