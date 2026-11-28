// k6: 20 phiên Giám khảo đồng thời (File 6 §2 hàng 4). CHƯA CHẠY trên máy dev (thiếu k6).
// Chạy (người vận hành, trỏ vào STAGING, không phải production thật):
//   k6 run -e BASE_URL=https://<staging-domain> loadtest/k6_judges.js
//
// Phạm vi kịch bản này (REST công khai, không cần chữ ký thiết bị):
//   - tạo phiên sandbox, bắn simulate-event, kiểm 0% rò rỉ chéo sandbox_namespace.
// KHÔNG bao phủ (cần luồng đăng ký thiết bị ký ECDSA / WebSocket ticket): contract-stream SSE
// "P95 < 4.2s" và WS "P95 < 250ms" — cần mở rộng riêng (xem loadtest/README.md).
import http from 'k6/http';
import { check, sleep } from 'k6';
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
    namespace_leak: ['rate==0'],                 // 0% rò rỉ chéo namespace
    http_req_failed: ['rate<0.01'],
    simulate_event_ms: ['p(95)<250'],            // ngưỡng WS P95<250ms áp tạm cho simulate-event REST
    'http_req_duration{name:healthz}': ['p(95)<150'], // TTFB zero-cold-start < 150ms
  },
};

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export default function () {
  const h = http.get(`${BASE_URL}/healthz`, { tags: { name: 'healthz' } });
  check(h, { 'healthz 200': (r) => r.status === 200 });

  const created = http.post(`${BASE_URL}/api/v1/sandbox/sessions`, JSON.stringify({}), {
    headers: JSON_HEADERS,
    tags: { name: 'sandbox_session' },
  });
  // 201 = tạo được; 429 = bị giới hạn theo IP/ngân sách (hợp lệ khi 20 VU chung 1 IP) → bỏ lượt.
  if (created.status === 429) {
    sleep(1);
    return;
  }
  const ok = check(created, { 'session 201': (r) => r.status === 201 });
  if (!ok) return;
  const sess = created.json();

  const res = http.post(
    `${BASE_URL}/api/v1/sandbox/simulate-event`,
    JSON.stringify({ event_type: 'INCOMING_SCAM_CALL', params: { fiber_key: `vu${__VU}` } }),
    {
      headers: { ...JSON_HEADERS, 'X-Sandbox-Session': sess.sandbox_session_token },
      tags: { name: 'simulate_event' },
    },
  );
  simulateMs.add(res.timings.duration);
  if (res.status === 200) {
    namespaceLeak.add(res.json().sandbox_namespace !== sess.sandbox_namespace);
  }
  sleep(1);
}
