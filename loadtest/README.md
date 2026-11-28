# loadtest/

`k6_judges.js`: 20 phiên đồng thời (File 6 §2 hàng 4). Chưa chạy trên máy dev vì thiếu `k6`; chưa có số p95 nào.

```bash
k6 run -e BASE_URL=https://<staging> loadtest/k6_judges.js
```

Ngưỡng trong script: `namespace_leak == 0`, `http_req_failed < 1%`, `simulate_event_ms p95 < 250ms`,
`healthz p95 < 150ms`.

Chưa bao phủ (cần đăng ký thiết bị ECDSA + ws-ticket): quét hợp đồng SSE `P95 < 4.2s` và WebSocket
`P95 < 250ms`. 20 VU chung IP có thể chạm 429 theo IP (`SANDBOX_MAX_SESSIONS_PER_IP_PER_HOUR`); script bỏ
lượt khi 429, không tính là lỗi. Không chạy vào production thật.
