# loadtest/

`k6_judges.js`: 20 phiên đồng thời (File 6 §2 hàng 4). Chưa chạy trên máy dev vì thiếu `k6`; chưa có số p95 nào.

```bash
k6 run -e BASE_URL=https://<staging> loadtest/k6_judges.js
```

Ngưỡng trong script: `namespace_leak == 0`, `http_req_failed < 1%`, `simulate_event_ms p95 < 250ms` (tạm),
`healthz p95 < 150ms`.

- Mỗi VU dùng đúng 1 phiên sandbox tạo ở `setup()`. Chạy 20 VU từ MỘT IP tiêu tốn 20 phiên trong trần
  `SANDBOX_MAX_SESSIONS_PER_IP_PER_HOUR` (mặc định 30): chạy lần 2 trong cùng giờ sẽ bị 429 ở `setup()`.
  Ở staging hãy nâng biến này (>= số VU x số lần chạy mỗi giờ) hoặc chờ hết cửa sổ.
- Phép kiểm namespace chỉ là kiểm tạm trên phản hồi REST của chính VU; chưa chứng minh cô lập qua SSE/WS.
- CHƯA bao phủ (cần task bổ sung, vì phải đăng ký thiết bị ECDSA + ws-ticket): quét hợp đồng SSE
  `P95 < 4.2s` và WebSocket `P95 < 250ms`.
- Không chạy vào production thật.
