---
name: plan-feature
description: Bước 1 của workflow team agent. Opus đọc đặc tả momo/*.md + codebase rồi lập plan chia thành các task độc lập (vùng file không giao nhau, chia wave) lưu ở .claude/plans/<slug>.md. Dùng khi người dùng nói "lập plan", "plan cho WP...", "chia task", "/plan-feature <mô tả>".
---

# plan-feature — Opus lập kế hoạch có thể chạy song song

Đầu vào: mô tả tính năng / mã work package (vd `WP1-03`, `WP2-02`) / mốc (`M1`). Args: `$ARGUMENTS`.
Đầu ra: `.claude/plans/<slug>.md` theo `.claude/templates/plan.md`, `status: draft`.

## Quy trình
1. **Hiểu yêu cầu.** Nếu mơ hồ (không rõ WP nào, phạm vi đến đâu) → hỏi người dùng tối đa 3 câu rồi mới làm.
2. **Đọc nguồn sự thật.** `momo/team_division_2_groups.md` để lấy WP, DoD, vùng thư mục; rồi đọc ĐÚNG các mục đặc tả được tham chiếu (File 1..6). Không bịa yêu cầu không có trong đặc tả.
3. **Đọc codebase hiện tại.** Nếu task rộng, gọi agent `planner` (model opus, read-only) để khảo sát song song: cấu trúc thư mục, hợp đồng `contracts/`, code đã có, test đã có. Ghi nhận cái gì đã xong để không giao lại.
4. **Chia task** theo luật:
   - Mỗi task = 1 agent làm được trong ~1 phiên (≤ ~400 dòng thay đổi), có DoD kiểm chứng được bằng lệnh.
   - Mỗi task có `owns` (glob file được sửa). **Task cùng wave không được giao nhau**, kể cả file chung (`package.json`, lockfile, `pyproject.toml`, `docker-compose.yml`, `__init__.py` gốc, router tổng). File chung → giao cho đúng 1 task hoặc tách thành task wave 0.
   - Hợp đồng (`contracts/*`, schema, type dùng chung) phải tồn tại trước wave 1; nếu chưa có → T00 ở wave 0 chạy một mình.
   - Task phụ thuộc kết quả task khác → wave sau.
   - Mỗi task ghi rõ spec ref + mã edge case (`EC-...`) phải có test.
   - Chọn model: `sonnet` cho code thông thường, `opus` cho lõi toán/bảo mật/kiến trúc khó.
5. **Tự kiểm plan** trước khi trình: chạy kiểm tra giao nhau `owns` giữa các task cùng wave (so glob bằng mắt + liệt kê file cụ thể); mỗi DoD có lệnh verify; không task nào sửa `momo/**`.
6. **Ghi file** `.claude/plans/<slug>.md` (điền bảng mục 6 "Trạng thái thực thi" với `pending`).
7. **Trình người dùng duyệt**: tóm tắt số task, số wave, task nào song song, rủi ro chính. Chờ "OK/approved". Khi được duyệt → đổi `status: approved`.

## Không làm
- Không viết code sản phẩm ở bước này.
- Không tạo branch/worktree (đó là `/dispatch-tasks`).
