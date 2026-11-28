// k6: 20 phiên Giám khảo đồng thời (File 6 §2 hàng 4). CHƯA CHẠY trên máy dev (thiếu k6).
// Chạy (người vận hành, trỏ vào STAGING, không phải production thật):
//   k6 run -e BASE_URL=https://<staging-domain> loadtest/k6_judges.js
//
// Mỗi VU dùng ĐÚNG 1 phiên sandbox tạo trong setup() (20 phiên/lần chạy), không tạo phiên mỗi vòng lặp,
// để không vượt trần SANDBOX_MAX_SESSIONS_PER_IP_PER_HOUR (mặc định 30) -> 429. Chạy 20 VU từ MỘT IP
// ngay sau lần chạy trước cũng có thể chạm trần: ở staging hãy nâng SANDBOX_MAX_SESSIONS_PER_IP_PER_HOUR
// (>= số VU x số lần chạy trong giờ) hoặc chờ hết cửa sổ 1 giờ.
//
// Phạm vi (REST công khai, không cần chữ ký thiết bị):
//   - simulate-event + kiểm "0% rò rỉ chéo sandbox_namespace" — ĐÂY CHỈ LÀ PHÉP KIỂM TẠM (phản hồi của
//     chính VU có đúng namespace của mình không), CHƯA chứng minh cô lập sự kiện qua SSE/WS.
// CHƯA BAO PHỦ (cần task bổ sung: đăng ký thiết bị ECDSA + ws-ticket): quét hợp đồng SSE
// "P95 < 4.2s" và WebSocket "P95 < 250ms". Ngưỡng simulate_event_ms ở dưới chỉ là ngưỡng tạm.
import http from 'k6/http';
import { check, fail, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/$/, '');
const VUS = parseInt(__ENV.VUS || '20', 10);

const namespaceLeak = new Rate('namespace_leak');
const simulateMs = new Trend('simulate_event_ms', true);

export const options = {
  scenarios: {
    judges: { executor: 'constant-vus', vus: VUS, duration: __ENV.DURATION || '60s' },
  },
  thresholds: {
    namespace_leak: ['rate==0'],                       // 0% rò rỉ chéo namespace (kiểm tạm)
    http_req_failed: ['rate<0.01'],
    simulate_event_ms: ['p(95)<250'],                  // tạm áp cho simulate-event REST (WS P95<250ms chưa đo)
    'http_req_duration{name:healthz}': ['p(95)<150'],  // TTFB zero-cold-start < 150ms
  },
};

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function setup() {
  const sessions = [];
  for (let i = 0; i < VUS; i++) {
    const res = http.post(`${BASE_URL}/api/v1/sandbox/sessions`, JSON.stringify({}), {
      headers: JSON_HEADERS,
      tags: { name: 'sandbox_session' },
    });
    if (res.status !== 201) {
      fail(`không tạo được phiên #${i} (HTTP ${res.status}); có thể chạm trần phiên/IP/giờ — xem README`);
    }
    const body = res.json();
    sessions.push({ token: body.sandbox_session_token, namespace: body.sandbox_namespace });
  }
  return { sessions };
}

export default function (data) {
  const mine = data.sessions[(__VU - 1) % data.sessions.length];

  const h = http.get(`${BASE_URL}/healthz`, { tags: { name: 'healthz' } });
  check(h, { 'healthz 200': (r) => r.status === 200 });

  // 429 (hạn mức/ngân sách sandbox) là hành vi hợp lệ, không tính là lỗi http_req_failed.
  const res = http.post(
    `${BASE_URL}/api/v1/sandbox/simulate-event`,
    JSON.stringify({ event_type: 'INCOMING_SCAM_CALL', params: { fiber_key: `vu${__VU}` } }),
    {
      headers: { ...JSON_HEADERS, 'X-Sandbox-Session': mine.token },
      tags: { name: 'simulate_event' },
      responseCallback: http.expectedStatuses(200, 429),
    },
  );
  if (res.status === 200) {
    simulateMs.add(res.timings.duration);
    namespaceLeak.add(res.json().sandbox_namespace !== mine.namespace);
  }
  sleep(1);
}
