|||||||| verified || pass (1922✓); PR#8 CI pg xanh | review đang chạy |||||||| verified || pass (1927✓); PR#6 CI pg+redis xanh | sửa 2 vòng (1 blocker + 6 major), re-review vòng 3 đang chạy |||||||| verified || pass (433✓); PR#7 CI xanh | vòng 4: 2 blocker + 2 major (số sai câu thường ngày), đang sửa vòng 5 |||||||| verified || pass (1922✓); PR#8 CI pg xanh | review đang chạy |||||||| verified || pass (1927✓); PR#6 CI pg+redis xanh | sửa 2 vòng (1 blocker + 6 major), re-review vòng 3 đang chạy |||||||| verified || pass (433✓); PR#7 CI xanh | vòng 4: 2 blocker + 2 major (số sai câu thường ngày) → đang sửa vòng 5 # PROGRESS — Backend VayShield AI

Plan: [`../backend-vayshield.md`](../backend-vayshield.md) · Cập nhật %: `node .claude/scripts/progress.js backend-vayshield --write`

<!-- progress:start -->
**Tổng tiến độ: 64.4%** `[█████████████░░░░░░░]` — merged 10/19 task · cập nhật 2026-11-27

| Wave | Tiến độ | | Mốc | Tiến độ |
|---|---|---|---|---|
| W0 | 100% | | M0 | 100% |
| W1 | 93% | | M1 | 82% |
| W2 | 100% | | M2 | 47% |
| W3 | 0% | | M3 | 50% |
| W4 | 0% | | M4 | 0% |
| W5 | 0% | |  |  |
<!-- progress:end -->

## Trạng thái & cách tính
| Trạng thái | Nghĩa | Tính điểm |
|---|---|---|
| `pending` | chưa bắt đầu | 0% |
| `running` | agent đang làm | 20% |
| `implemented` | agent đã commit trên branch | 50% |
| `verified` | `/verify` pass | 70% |
| `reviewed` | `/review-branch` approve | 85% |
| `merged` | đã vào `main` | 100% |
| `blocked` | chờ người quyết định | giữ nguyên điểm trước đó (ghi lý do) |
| `skip` | không thuộc phần việc của tôi | loại khỏi mẫu số |

**Trọng số** lấy từ tỷ trọng WP trong `team_division_2_groups.md` §2, quy về thang 100% của backend.

## Bảng task
| ID | WP | Task | Wave | Mốc | Phụ thuộc | Trọng số | Trạng thái | Branch | Verify | Review | Cập nhật | Ghi chú |
|----|----|------|------|-----|-----------|---------:|-----------|--------|--------|--------|----------|---------|
| T00 | WP1-06 | Khung FastAPI + contracts M0 | 0 | M0 | – | 2 | merged | task/backend-vayshield/T00 | pass (95✓ 1 skip pg) | approve (sau 1 vòng sửa) | 2026-11-25 | Cần Nhóm 2 duyệt contract |
| T01 | WP1-01 | PostgreSQL schema + RLS + hash-chain | 1 | M1 | T00 | 10 | merged | task/backend-vayshield/T01 | pass local + CI pg (PR#1) | approve sau sửa (2 major); CI pg xanh | – | |
| T02 | WP1-03 | Kernel tài chính APR/XIRR/khoảng/MIRR | 1 | M1 | T00 | 8 | merged | task/backend-vayshield/T02 | pass (177✓) | approve sau sửa (1 major); bản sửa chỉ verify, chưa review lại | – | ≥35 pytest |
| T03 | WP1-04 | Thị giác đối kháng + VietQR | 1 | M1 | T00 | 10 | merged | task/backend-vayshield/T03 | pass (210✓) sau sửa | approve sau 2 vòng sửa (1 blocker + 7 major); vòng 2 chỉ verify, chưa review lại | – | |
| T04 | WP1-05 | Trích xuất LLM + Conformal + Voice | 1 | M1 | T00 | 12 | verified | task/backend-vayshield/T04 | pass (311✓) sau sửa | re-review đang chạy | – | |
| T05 | WP1-07 | Redis HSM + Lua smurf + idempotency | 1 | M2 | T00 | 8 | merged | task/backend-vayshield/T05 | pass (462✓) | approve sau 2 vòng sửa (2 blocker + 6 major); vòng 2 chỉ verify + CI Redis thật xanh, chưa review lại | – | |
| T06 | WP1-11 | Dataset Bench-340 (data + nhãn) | 1 | M3 | T00 | 2 | merged | task/backend-vayshield/T06 | validate pass | approve; nhãn APR đối chiếu solver T02: 63 trên 63 khớp, 17 null (advance-fee) | – | |
| T07 | WP1-08 | Ontology 10 trục C1–C10 | 1 | M2 | T00 | 4 | merged | task/backend-vayshield/T07 | pass (188✓) | approve (sau 1 vòng sửa) | – | |
| T08 | WP1-02 | Seed registry + luật + mule mẫu | 2 | M2 | T01 | 6 | merged | task/backend-vayshield/T08 | pass (881✓); PR#4 CI pg | approve sau sửa (4 major); bản sửa chỉ verify + CI pg xanh, chưa review lại; dữ liệu verified=false cần người xác minh | – | Người xác minh dữ liệu thật |
| T09 | WP1-03 | Ma trận 9 phán quyết + lookalike | 2 | M1 | T02,T01 | 4 | merged | task/backend-vayshield/T09 | pass (922✓); PR#5 CI pg | approve sau 3 vòng sửa (1 blocker + nhiều major); vòng 3 chỉ verify + CI pg xanh + bộ hồi quy 354 tên, chưa review lại | – | |
| T10 | WP1-06 | Mật mã & bảo mật nền | 2 | M1 | T01 | 2 | merged | task/backend-vayshield/T10 | pass (539✓); PR#3 CI pg | approve sau sửa (2 major); CI pg xanh; rủi ro mở: chữ ký policy chỉ phủ payload cần task contract | – | |
| T11 | WP1-06 | API Trạm 1 & 2 (SSE) | 3 | M1 | T02,T03,T04,T09,T10 | 6 | pending | task/backend-vayshield/T11 | – | – | – | |
| T12 | WP1-08 | API Trạm 3 + WS + leo thang + gia đình | 3 | M2 | T05,T07,T10 | 6 | pending | task/backend-vayshield/T12 | – | – | – | |
| T13 | WP1-02 | Legal GraphRAG | 3 | M2 | T08 | 2 | pending | task/backend-vayshield/T13 | – | – | – | |
| T14 | WP1-09 | Mule graph + Dynamic VA + B2B webhook | 3 | M2 | T01,T10 | 5 | pending | task/backend-vayshield/T14 | – | – | – | |
| T15 | WP1-09 | Copilot + SIMO + triage + TTS | 4 | M2 | T13,T14 | 3 | pending | task/backend-vayshield/T15 | – | – | – | |
| T16 | WP1-10 | Sandbox Mode + VIP quota | 4 | M2 | T11,T12 | 4 | pending | task/backend-vayshield/T16 | – | – | – | |
| T17 | WP1-11 | Runner benchmark + ablation | 4 | M3 | T06,T11,T12 | 2 | pending | task/backend-vayshield/T17 | – | – | – | Gọi Gemini thật: cần duyệt |
| T18 | WP1-12 | Cloud + Docker + gitleaks + k6 | 5 | M4 | tất cả | 4 | pending | task/backend-vayshield/T18 | – | – | – | Deploy cần duyệt |

## Nhật ký
| Ngày | Sự kiện |
|---|---|
| 2026-11-25 | Tạo plan + bảng progress (status: draft, chờ duyệt) |
| 2026-11-25 | Plan approved; T00 dispatched (wave 0) |
| 2026-11-25 | T00 implemented (PARTIAL: .env.example bị chặn quyền, chưa chạy Docker) → verify pass |
| 2026-11-25 | T00 review: 4 major + 9 minor → sửa xong (a0ef22f) → merged. Mốc M0 xong (contract chờ Nhóm 2 xem) |
| 2026-11-25 | Wave 1 dispatched: T01–T07 song song (7 worktree) |
| 2026-11-25 | Giới hạn API 5h làm dừng T02–T06 + review T01 → đã resume. T07 review: 9 major (báo động giả do đồng âm không dấu) → đang sửa |
| 2026-11-25 | T02/T03 verified. T01 CI pg xanh, review 2 major → đang sửa. T06 nhãn dùng BLOCK/DANGER + XIRR thay vì 9 mã V_* + APR_VN_365 → đang sửa. T02 báo 3 số trong spec không khớp công thức (chờ xác nhận) |
| 2026-11-25 | T07 merged (approve (sau 1 vòng sửa)) |
| 2026-11-25 | T01 sửa xong (d58df8e), CI pg đang chạy lại. Reviewer T02 xác nhận 3 số trong spec sai (XIRR 6539,1%; S2-EC12 333,2%; §1.3 ~100%). T03 review: 1 blocker (bỏ lọt QR kẻ gian) → đang sửa |
| 2026-11-25 | Người dùng dừng toàn bộ agent. Handoff: .claude/handoff/2026-11-25-backend-vayshield-wave1.md |
| 2026-11-26 | Người dùng bảo tiếp tục. CI T01: 1 test fail (test lỗi thời sau khi nới CHECK) → agent T01 sửa. T02, T03: agent mới chạy trên worktree cũ để làm nốt sửa review. T04–T06 để lượt sau (giữ ≤3 agent/lượt) |
| 2026-11-26 | T01 merged (approve sau sửa (2 major); CI pg xanh) |
| 2026-11-26 | T02 merged (approve sau sửa (1 major); bản sửa chỉ verify, chưa review lại) |
| 2026-11-26 | T01, T02 merged. T03 sửa xong → re-review. T05 implemented (95,7% khớp spec) → PR#2 cho CI Redis thật + review. T04 agent mới đang làm nốt. T06 chờ lượt sau |
| 2026-11-26 | T05 review: 2 blocker (fail-open khi Redis sống + cache trống + PG lỗi), 4 major → agent đang sửa. CI PR#2 đỏ vì isort I001 (verify.sh chạy ruff check . vẫn sạch → lệch cấu hình, agent tra log) |
| 2026-11-26 | T03 re-review: blocker cũ đã sửa; sinh 3 major mới (SELF trên nền xanh, hồi quy _FEE, CPU/RAM detect_qr_codes) → agent đang sửa vòng 2 |
| 2026-11-26 | T04 implemented + verified (113 test riêng; schema int đồng thay float, có chủ đích) → review đang chạy. Đang chạy song song: T03 sửa vòng 2, T05 sửa blocker, T04 review |
| 2026-11-26 | T03 merged (approve sau 2 vòng sửa (1 blocker + 7 major); vòng 2 chỉ verify, chưa review lại) |
| 2026-11-26 | T03 merged (419✓). T04 review: 2 blocker (số tiền lóng: "10 triệu một tháng" → 10.100.000) + 7 major (gộp nhầm 2 thương hiệu, mất phí khi hợp nhất Pro, pool key dùng chung...) → agent đang sửa. T06 agent mới đang sửa nhãn |
| 2026-11-26 | T06 merged (nhãn APR đối chiếu solver T02: 63 trên 63 khớp, 17 null do advance-fee). T05 sửa xong, CI Redis thật chạy, re-review đang chạy |
| 2026-11-26 | T05 re-review: 6 lỗi cũ đã sửa, CI Redis thật xanh (760✓, 0 skip); còn 2 major mới (expire dọn S5-S9 quá sớm; E3 trên key default fail-open) → agent sửa vòng 2. T10 (mật mã nền) dispatch sớm vì chỉ phụ thuộc T01 |
| 2026-11-26 | T04 sửa xong 13 mục (311✓) → re-review (trọng tâm: 40+ câu số tiền mới) đang chạy. Đang chạy: T05 vòng 2, T10, T04 re-review |
| 2026-11-26 | Người dùng dừng rồi bảo tiếp tục. T05 vòng 2 verified (462✓) + đã push → CI Redis thật. T04 re-review (reviewer mới) và T10 (agent mới, worktree cũ) đang chạy |
| 2026-11-26 | T05 merged (approve sau 2 vòng sửa (2 blocker + 6 major); vòng 2 chỉ verify + CI Redis thật xanh, chưa review lại) |
| 2026-11-26 | T05 merged (CI Redis thật 774✓, 0 skip). T09 dispatched (VerdictInput độc lập vì T04 chưa merge). Đang chạy: T04 re-review, T10, T09. Đã ghi chú T05→T12 vào prompt T12 |
| 2026-11-26 | T10 review: 2 major (replay ECDSA n-s, policy bundle chỉ ký payload) → sửa (6a6cb22), PR#3 cho CI pg, re-review đang chạy. T09 implemented + verified (891✓), review đang chạy. T04: gửi 2 blocker + 2 major + 1 minor, đổi chiến lược không đoán khi mơ hồ |
| 2026-11-26 | T10 merged (approve sau sửa (2 major); CI pg xanh; rủi ro mở: chữ ký policy chỉ phủ payload cần task contract) |
| 2026-11-26 | Giới hạn 5h dừng T09 (đang sửa review), T04 (review vòng 3), T08 (mới bắt đầu); reset 21:30. Quyết định của người dùng: ngân hàng/FinCo chỉ có ảnh chụp, chưa xác thực APK → tối đa VÀNG (AMBER, LENDER_NOT_VERIFIED); gửi cho T09 khi agent chạy lại. P2P 20–35% chờ quyết |
| 2026-11-26 | T08 implemented + verified (881✓, mọi dữ liệu registry/luật verified=false), PR#4. T09 sửa xong (75a70ff), PR#5, re-review. T04 review vòng 3: 2 blocker + 4 major → agent sửa theo hướng whitelist ranh giới |
| 2026-11-26 | T08 merged (approve sau sửa (4 major); bản sửa chỉ verify + CI pg xanh, chưa review lại; dữ liệu verified=false cần người xác minh) |
| 2026-11-27 | T09 merged (approve sau 3 vòng sửa (1 blocker + nhiều major); vòng 3 chỉ verify + CI pg xanh + bộ hồi quy 354 tên, chưa review lại) |
| 2026-11-27 | T09 merged (10/19). T12 sửa 2 vòng + sửa main.py lifespan (được phép), T14 implemented + repo SQL, cả hai CI xanh, đang review. T04 vòng 4 còn 2 blocker số sai → agent sửa vòng 5 |
