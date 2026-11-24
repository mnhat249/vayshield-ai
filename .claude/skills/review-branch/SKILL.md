---
name: review-branch
description: Claude thứ hai review độc lập kết quả của Claude thứ nhất, như một staff engineer — logic, bảo mật, case chưa test, độ phức tạp, ảnh hưởng ngầm, lệch hợp đồng API. Dùng sau /verify, hoặc khi nói "review branch", "review task T03", "/review-branch <slug> [ID] | <branch>".
---

# review-branch — Review với context sạch

Args: `$ARGUMENTS` = `<slug>` [ID...] hoặc tên branch.

## Quy trình
1. Với mỗi branch, gọi agent `reviewer` (model opus, context mới, KHÔNG gửi báo cáo tự khen của implementer — chỉ gửi: branch, base, plan task section, spec refs). Nhiều branch → nhiều reviewer song song trong 1 message.
2. Reviewer dùng skill `code-reviewer` và trả lời 5 câu bắt buộc:
   1. Lỗi logic nào? (kèm kịch bản input → output sai)
   2. Rủi ro bảo mật nào? (secret, injection, RLS, HMAC, PII/OTP trong log)
   3. Case nào chưa test? (đối chiếu edge case `EC-...` trong spec)
   4. Phần nào quá phức tạp / có thể đơn giản?
   5. Thay đổi nào ảnh hưởng ngầm hệ thống? (hợp đồng `contracts/`, schema DB, file chung, hiệu năng)
   Mỗi phát hiện: severity `blocker | major | minor | nit`, file:line, đề xuất sửa.
3. Ghi kết quả vào cột Review của plan: `approve` / `changes(<số blocker>/<major>)`.
4. Có blocker/major → tạo prompt sửa ngắn (cùng branch, cùng owns) và gửi lại implementer; sau khi sửa chạy lại `/verify` + `/review-branch`.
5. Trình người dùng: bảng các branch + verdict. Branch `approve` + verify `pass` mới được đề xuất `/integrate-branches`.
