# Runner benchmark VayShield-Bench-340 + 3 ablation (T17)

```bash
# từ gốc repo; 1 lệnh, OFFLINE (Gemini giả), tái lập (không dấu thời gian)
uv run --project backend python benchmark-dataset/runner/run_all.py --quick   # -> results/quick/
uv run --project backend python benchmark-dataset/runner/run_all.py           # -> results/
uv run --project backend pytest benchmark-dataset/runner -q                   # test runner
```

Đầu ra: `summary.md` (bảng metric + SLA + mục "chỗ chưa đạt/chưa đo"), `metrics.csv` (Nhóm 2 dùng cho slide 8),
`contract_per_sample.csv`.

## Quy tắc trung thực
- Mặc định **không gọi mạng**. Bước "đọc ảnh" của Gemini được thay bằng *oracle* dựng từ nhãn chuẩn
  (`FakeGeminiClient`) -> số liệu hợp đồng chỉ kiểm hạ tầng + bộ giải tài chính tất định, **không phải
  độ chính xác của Gemini thật**. Baseline "Pure LLM" ở Ablation 1 là mô phỏng công thức ngây thơ.
- Voice/Coercion/Notif/QR/Mule chạy trên code backend tất định thật (không LLM).
- Chế độ LIVE chỉ bật khi `BENCH_LIVE=1` **và** `BENCH_GEMINI_API_KEY` có trong môi trường process
  (runner không đọc `.env*`). Khi bật in cảnh báo chi phí/gửi dữ liệu; tối đa `BENCH_LIVE_MAX_CALLS`
  (mặc định 20) lần gọi, chỉ Ablation 1 (dòng tiền tổng hợp). Đường LIVE chưa được chạy với mạng thật.
- Chỉ số không đo được hiển thị `NOT_MEASURED` kèm lý do (Spoof recall, Disbursement F1).
- SLA chưa đạt không làm lệnh thất bại (exit 0); xem `summary.md`.
