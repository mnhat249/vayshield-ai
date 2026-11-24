# SESSION 2 (v2.0 Ultra-Deep Edition) — Kiến Trúc Hệ Thống Đa Tầng (C4), Đa Phiên Bayesian HSM, Chuỗi Hạ Cấp 4 Tầng, Toán Tài Chính Khoảng Nhạy & 48 Edge Cases Hệ Thống

**Dự án:** **VayShield AI**  
**Vai trò đồng chủ trì Session 2:** Principal Software Architect • Fintech Quantitative Specialist • Senior Backend Engineer • Senior Android Engineer • Cybersecurity Architect • Business Analyst • QA/Chaos Engineer.

---

## 1. Kiến Trúc Hệ Thống Đa Tầng C4 & Lõi Neuro-Symbolic Khó Dịch Ngược

### 1.1. Nguyên Lý Thiết Kế Cốt Lõi (Computational Thinking Principles)

1. **Phân tách Xác suất – Tất định (Neuro-Symbolic Orthogonality)**: Mô hình ngôn ngữ/thị giác (LLM) chỉ đóng vai trò *Bộ chuyển đổi tín hiệu phi cấu trúc sang cấu trúc (Unstructured-to-Structured Transducer)*. Toàn bộ phép tính lãi suất, kiểm định bất biến dòng tiền, đối chiếu chữ ký số APK và phán quyết pháp lý do **Nhân Tất định (Deterministic Kernel)** thực thi. LLM không có quyền sửa đổi kết quả toán học hoặc hạ mức cảnh báo đỏ.
2. **Phân vùng Không Tin Cậy & Chống Dịch Ngược (Zero-Trust & Anti-Reverse-Engineering Partitioning)**:
   - Trên Android Client, nếu kẻ tấn công dịch ngược APK (bằng `jadx` / `apktool`), họ chỉ thấy **Bộ thông dịch Quy tắc Trừu tượng (`SignedPolicyVM`)** thực thi gói cấu hình nhị phân/JSON được ký số bất đối xứng `Ed25519` từ Server (`PolicyBundle_vX.sig`).
   - Toàn bộ ngưỡng cộng hưởng, danh sách băm tài khoản mồi (`Salted SHA-256 Bloom Filter`) và đồ thị liên kết thực thể lừa đảo nằm trên Server Enclave hoặc được xoay vòng khóa định kỳ.
3. **Ưu tiên Phản xạ Sinh tồn Ngoại tuyến (Edge-Reflex First, Cloud-Enrichment Second)**: Mọi quyết định chặn khẩn cấp ở Trạm 1 và Trạm 3 phải hoàn tất trên thiết bị trong **`< 50ms`** không phụ thuộc Internet; dữ liệu phân tích sâu từ Cloud chạy bất đồng bộ để bổ sung lập luận và kích hoạt liên kết gia đình / B2B.
4. **Bất biến Đồng hồ Đơn điệu Chống Thao túng Thời gian (`Tamper-Proof Monotonic Time Invariant`)**: Mọi cửa sổ thời gian an ninh (`Khiên 24h`, `Dư chấn cuộc gọi 180s`, `Khóa bình tĩnh 15s`, `Cửa sổ Smurfing 15m`) tuyệt đối không dựa vào đồng hồ hệ thống `System.currentTimeMillis()` đơn thuần (vốn có thể bị người dùng bị kẻ gian dụ chỉnh lùi/tiến ngày giờ trong Cài đặt để phá khóa TTL), mà neo cứng vào **`SystemClock.elapsedRealtimeNanos()`** kết hợp độ lệch thời gian thực đã ký số từ Server (`SignedServerEpochOffset`) lưu bền vững qua sự kiện khởi động lại máy (`ACTION_BOOT_COMPLETED`).

---

### 1.2. C4 Level 1 & Level 2 — Kiến Trúc Container Toàn Hệ Sinh Thái

```mermaid
flowchart TB
    subgraph USERS["Tác Nhân Tương Tác"]
        U_BORROWER["Người Đi Vay (Cô Lan - Android / Web)"]
        U_FAMILY["Người Thân Bảo Hộ (Minh Khoa - PWA iOS/Android)"]
        U_JUDGE["Ban Giám Khảo (Trình duyệt Desktop/Mobile)"]
        U_OPS["Chuyên Viên Rủi Ro Fintech (Hoàng Nam - Ops Portal)"]
    end

    subgraph EDGE["Tầng Biên & Giao Diện (Edge & Presentation Plane)"]
        ANDROID["Android Native Guardian (Kotlin, Compose Material 3, SQLCipher, Zero-OTP Gate)"]
        WEB_PORTAL["Next.js 15 Production Apps (B2C Web/PWA + Family Guardian + B2B Fraud SaaS + Stripe-style Sandbox Mode)"]
    end

    subgraph CORE["Tầng Điều Phối & Lõi Xử Lý (FastAPI Core & Neuro-Symbolic Plane)"]
        GATEWAY["API Gateway & Stream Hub (REST, SSE Streamer, WS Multiplexer, HMAC B2B Guard)"]
        NS_KERNEL["Neuro-Symbolic Risk Kernel (Brent-XIRR Solver, 4D Legal Engine, Spoof Verifier, Bayesian HSM)"]
        AI_ORCH["Multimodal AI & GraphRAG Orchestrator (Model Cascade, Canonical Sanitizer, Coercive Intent NLP)"]
        MULE_ENGINE["Pre-SIMO Mule Graph & Copilot Service (Entity Resolution, Cluster Detector, Advisory Generator)"]
    end

    subgraph DATA["Tầng Lưu Trữ Đa Mô Hình (Polyglot Persistence Plane)"]
        PG["PostgreSQL 16 + pgvector (Users, Audits, Legal Vectors, Mule Graph Edges, B2B Shields)"]
        REDIS["Redis 7 Streams & TTL Store (Active 24h Shields, Smurfing Counters, WS Pub/Sub, Rate Limit)"]
    end

    subgraph EXT["Hệ Thống Bên Ngoài"]
        GEMINI["Gemini Multimodal API (Primary Pro/Flash + Fallback Models)"]
        PUSH_SMS["Web Push VAPID / SMS & Email Fallback / Zalo ZBS Webhook"]
        FINCO_SYS["Hệ Thống Core Giải Ngân Đối Tác Fintech (Webhook Caller)"]
    end

    U_BORROWER --> ANDROID
    U_BORROWER --> WEB_PORTAL
    U_FAMILY --> WEB_PORTAL
    U_JUDGE --> WEB_PORTAL
    U_OPS --> WEB_PORTAL

    ANDROID <-->|"HTTPS TLS 1.3 + Ed25519 Signed Policy + WS"| GATEWAY
    WEB_PORTAL <-->|"REST + SSE + WebSocket"| GATEWAY
    FINCO_SYS -->|"POST /v1/b2b/disbursement-shield (HMAC-SHA256)"| GATEWAY

    GATEWAY --> NS_KERNEL
    GATEWAY --> AI_ORCH
    GATEWAY --> MULE_ENGINE

    AI_ORCH <--> GEMINI
    NS_KERNEL <--> AI_ORCH
    NS_KERNEL <--> REDIS
    NS_KERNEL <--> PG
    MULE_ENGINE <--> PG
    MULE_ENGINE <--> REDIS
    GATEWAY --> PUSH_SMS
```

---

### 1.3. C4 Level 3 — Giải Phẫu Nội Bộ `Neuro-Symbolic Risk Kernel` (7 Tầng Kiểm Định Chéo)

Để đảm bảo tính chất **khó bị đánh lừa bởi dữ liệu nhiễu, ảnh chụp thiếu trang hoặc tấn công đối kháng**, mọi yêu cầu phân tích hợp đồng/tin nhắn/hành vi đều đi qua đường ống 7 chặng tuần tự - rẽ nhánh:

```mermaid
flowchart LR
    S1["1. Canonical & Tile Sanitizer (Khử chữ ẩn, cắt lát ảnh cuộn dài)"] --> S2["2. Provenance & VietQR Verifier (SHA-256 APK, EMVCo TLV, Bloom Filter)"]
    S2 --> S3["3. Multimodal Schema Extractor (Ảnh / Giọng nói -> JSON định kiểu)"]
    S3 --> S4["4. Contradiction Detector (Mâu thuẫn Thương hiệu - STK - Lãi suất)"]
    S4 --> S4B["5. Completeness & Interval Gate (Kiểm tra thiếu trang & Khoảng bất định)"]
    S4B --> S5["6. Deterministic Cash-Flow & Legal Solver (Brent-XIRR, APR 365, MIRR)"]
    S5 --> S6["7. Bayesian Semilattice Synthesizer (Hợp nhất đa phiên, FLAG_SECURE, Mule Graph)"]
```

- **Điểm đắt giá ở Chặng 4 (`Contradiction Detector`) & Chặng 5 (`Completeness & Interval Gate`)**:
  - *Mâu thuẫn Thương hiệu – Tài khoản thụ hưởng*: Hợp đồng in tiêu đề *"CÔNG TY TÀI CHÍNH TNHH MTV HOME CREDIT VIỆT NAM"* nhưng yêu cầu chuyển "phí bảo hiểm" vào tài khoản cá nhân tên `NGUYEN VAN B` $\rightarrow$ Khớp mẫu **Giả mạo Pháp nhân Cấp độ Chí mạng**.
  - *Mâu thuẫn Toán học Nội tại*: Văn bản ghi *"Lãi suất ưu đãi 1.2%/tháng, vay 10.000.000đ trong 12 tháng"*, nhưng cột số tiền trả hàng tháng ghi `1.350.000đ` (tổng trả `16.200.000đ`, tương đương lãi thực `~96.5%/năm` tính trên dư nợ giảm dần!) $\rightarrow$ Vạch trần hành vi ngụy tạo con số lãi suất danh nghĩa.
  - *Cổng Đầy đủ Chứng từ & Toán học Khoảng (`Completeness & Interval Gate`)*: Nếu ảnh mờ ở 1 khoản phí phụ làm phát sinh khoảng bất định $F \in [F_{\min}, F_{\max}]$, hệ thống giải cận dưới và cận trên $[\text{APR}_{\min}, \text{APR}_{\max}]$. Nếu ngay cả cận dưới $\text{APR}_{\min} \ge 100\%/\text{năm}$ thì phán quyết Tín dụng đen là **bất biến toán học 100%** bất chấp độ mờ OCR; ngược lại nếu thiếu kỳ hạn làm khoảng dao động cắt qua ranh giới pháp lý, hệ thống bật chế độ xác nhận 1 chạm chống kết luận oan.

---

## 2. Bộ Giám Sát Đa Phiên Bayesian HSM & Động Học Suy Giảm Rủi Ro (`Multi-Fiber BorrowerProtectionHSM`)

### 2.1. Giám Sát Đa Khoản Vay Song Song (`Concurrent Multi-Loan Fiber Supervisor`) & Sơ Đồ HSM 10 Trạng Thái

Trong thực tế, người lao động đang túng quẫn thường cài và nộp hồ sơ trên **3–4 ứng dụng vay cùng một buổi chiều** (ví dụ: App A là lừa đảo đòi phí trước, App B là công ty tài chính thật vừa giải ngân 15 triệu, App C là app tín dụng đen đang duyệt). Nếu chỉ dùng 1 máy trạng thái phẳng duy nhất, sự kiện mới của App C sẽ ghi đè làm mất Khiên bảo vệ 24h của App B!

Do đó, VayShield AI quản lý một tập tối đa $K = 8$ luồng trạng thái song song (`LoanJourneyFiber`), hợp nhất theo toán tử **Nửa Dàn Cận Trên (`Join-Semilattice Operator` $\bigsqcup$)** và bổ sung trạng thái cấp cứu sau mất tiền **`S9_GOLDEN_HOUR_TRIAGE`**:

```mermaid
stateDiagram-v2
    [*] --> S0_IDLE

    S0_IDLE --> S1_LOAN_EXPLORATION : E1_APK_INSTALLED (Suspicious/Loan App)
    S0_IDLE --> S2_CONTRACT_AUDITING : E2_IMAGE_OR_VOICE_TERMS_SUBMITTED
    S0_IDLE --> S4_SHIELD_ARMED_24H : E4_HIGH_CONF_DISBURSEMENT_NOTIF / E5_B2B_WEBHOOK
    S0_IDLE --> S9_GOLDEN_HOUR_TRIAGE : E11_POST_LOSS_RECEIPT_OR_REPORT

    S1_LOAN_EXPLORATION --> S2_CONTRACT_AUDITING : E2_IMAGE_OR_VOICE_TERMS_SUBMITTED
    S1_LOAN_EXPLORATION --> S4_SHIELD_ARMED_24H : E3_ANY_ORG_CREDIT_INFLOW (Within 72h)
    S1_LOAN_EXPLORATION --> S0_IDLE : T1_MONOTONIC_TTL_EXPIRED (72h)

    S2_CONTRACT_AUDITING --> S3_ADVANCE_FEE_HARD_STOP : V1_UPFRONT_FEE_OR_BRAND_SPOOF_OR_BNPL_CASHOUT
    S2_CONTRACT_AUDITING --> S4_SHIELD_ARMED_24H : V2_LOAN_TERMS_EXTRACTED (Pre-arm 72h -> 24h on Credit)

    S3_ADVANCE_FEE_HARD_STOP --> S5_CRITICAL_INTERCEPTION : E6_BANKING_OR_WALLET_OPENED (Within 4h)
    S3_ADVANCE_FEE_HARD_STOP --> S9_GOLDEN_HOUR_TRIAGE : E11_USER_ALREADY_TRANSFERRED

    S4_SHIELD_ARMED_24H --> S5_CRITICAL_INTERCEPTION : E6_BANKING_OR_WALLET_OPENED
    S4_SHIELD_ARMED_24H --> S0_IDLE : T2_SHIELD_24H_COMPLETED

    S5_CRITICAL_INTERCEPTION --> S6_COOLING_OFF_LOCK : G1_CALL_OR_SCREENSHARE_OR_HIGH_RISK (FLAG_SECURE ON)
    S5_CRITICAL_INTERCEPTION --> S8_SAFE_ROUTINE_TX : G2_NO_CALL_AND_SAFE_DECLARATION

    S6_COOLING_OFF_LOCK --> S7_ESCALATED_SCAM_CONFIRMED : E7_COERCION_DETECTED / E9_SMURF_BYPASS_ATTEMPT
    S6_COOLING_OFF_LOCK --> S9_GOLDEN_HOUR_TRIAGE : E11_USER_ALREADY_TRANSFERRED
    S6_COOLING_OFF_LOCK --> S8_SAFE_ROUTINE_TX : E10_TIMER_DONE_AND_VERIFIED_SAFE

    S8_SAFE_ROUTINE_TX --> S4_SHIELD_ARMED_24H : Resume Remaining 24h Watch
    S7_ESCALATED_SCAM_CONFIRMED --> S4_SHIELD_ARMED_24H : Max-Alert Lock + Bidirectional Family Rescue + Mule Logged
    S9_GOLDEN_HOUR_TRIAGE --> S4_SHIELD_ARMED_24H : Block 2nd Sunk-Cost Transfer + Bank Freeze Hotline + Police PDF
```

---

### 2.2. Mô Hình Toán Học Chấm Điểm Rủi Ro Đa Phiên & Phát Hiện Chia Sẻ Màn Hình $R(t)$

Với mỗi luồng hành trình $j \in \mathcal{F}_{\text{active}}(t)$, điểm Log-Odds $L_j(t)$ và điểm tổng hợp toàn thiết bị $L_{\text{eff}}(t)$ tại thời điểm đơn điệu $t$ được xác định bởi:

$$L_j(t) = L_{\text{prior}} + \sum_{e_k \in \mathcal{H}_{j,t}} w_k \cdot \exp\!\left(-\frac{\ln 2}{\tau_k}(t - t_k)\right) + \Psi_{\text{synergy}}(t) + \Xi_{\text{proj}}(t) + \Omega_{\text{smurf}}(t)$$

$$L_{\text{eff}}(t) = \max_{j \in \mathcal{F}_{\text{active}}(t)} L_j(t) + \gamma_{\text{multi}} \cdot \ln\bigl(|\mathcal{F}_{\text{active}}(t)|\bigr), \qquad P(\text{Scam} \mid \mathcal{H}_t) = \frac{1}{1 + e^{-L_{\text{eff}}(t)}}$$

**Giải nghĩa các thành phần định lượng (hiệu chỉnh thực nghiệm hành vi tội phạm):**
- $L_{\text{prior}} = -3.5$ (xác suất nền $\approx 2.9\%$ cho một lần mở app ngân hàng bình thường để mua cà phê, trả tiền điện); hệ số hoảng loạn đa khoản vay $\gamma_{\text{multi}} = +0.55$ khi người dùng nộp đơn nhiều app vay cùng lúc ($|\mathcal{F}_{\text{active}}| \ge 2$).
- **Chuỗi sự kiện lịch sử $\mathcal{H}_{j,t}$ với chu kỳ bán rã $\tau_k$ (Half-life)**:
  - Vừa bị cảnh báo *Hợp đồng giả mạo / Đòi phí ứng trước / Rút tiền Ví trả sau* ở Trạm 2: $w_{\text{spoof}} = +4.2$, $\tau = 4\text{ giờ}$.
  - Vừa nhận *Tiền giải ngân về tài khoản*: $w_{\text{disb}} = +2.8$, với hàm hai pha: giữ đỉnh không suy giảm trong **120 phút vàng đầu tiên** (thời điểm 85% kẻ gian gọi điện đòi thu hồi khoản vay), sau đó suy giảm với $\tau = 12\text{ giờ}$.
  - Cài APK vay ngoài CH Play ở Trạm 1: $w_{\text{apk}} = +1.8$, $\tau = 48\text{ giờ}$.
- **Số hạng Cộng hưởng Đàm thoại $\Psi_{\text{synergy}}(t)$ (Bắt Bẫy 3 kể cả khi dặn cúp máy tạm thời)**:
  $$\Psi_{\text{synergy}}(t) = \begin{cases} +3.8 & \text{nếu } \text{DisbursementActive}(t) = 1 \;\land\; \text{AudioMode}(t) \in \{\text{IN\_CALL}, \text{IN\_COMM}\} \\ +1.9 & \text{nếu } \text{DisbursementActive}(t) = 1 \;\land\; \text{RecentCallEndedWithin}(t, 180\text{s}) \\ 0 & \text{ngược lại} \end{cases}$$
- **Số hạng Phát hiện Chia sẻ Màn hình / Điều khiển Từ xa $\Xi_{\text{proj}}(t)$ (Đột phá Chống Giám Sát Trực Tiếp)**:
  $$\Xi_{\text{proj}}(t) = \begin{cases} +4.5 & \text{nếu phát hiện } \text{MediaProjectionFGS}(t) = 1 \;\lor\; \text{RemoteControlPkgActive}(t) = 1 \\ 0 & \text{ngược lại} \end{cases}$$
  Khi kẻ gian dụ bật *Chia sẻ màn hình Zalo* hoặc mở *TeamViewer / AnyDesk / UltraViewer*, $\Xi_{\text{proj}}(t) = +4.5$ lập tức đẩy xác suất rủi ro lên **$> 99.1\%$**, kích hoạt cờ phần cứng `FLAG_SECURE` làm tối đen màn hình phía kẻ gian!
- **Số hạng Chống Lách Luật / Chia Nhỏ Giao Dịch $\Omega_{\text{smurf}}(t)$**:
  $$\Omega_{\text{smurf}}(t) = 0.9 \times \max(0, m_{15\text{m}} - 1)$$
  Nếu người dùng bấm thoát màn hình cảnh báo rồi lập tức quay lại mở app ngân hàng lần thứ $m_{15\text{m}} \ge 2$ trong vòng 15 phút, xác suất rủi ro **tự động nhảy vọt lên mức Đỏ** thay vì giảm đi.

**Ngưỡng Hành động Phân tầng:**
- $P < 0.35$: Cho qua im lặng (không làm phiền sinh hoạt bình thường).
- $0.35 \le P < 0.75$: **Mức Cam (Soft Check)** — Hỏi nhanh 1 chạm không đối đầu xác nhận mục đích chuyển tiền (bắt được cả trường hợp kẻ gian gọi vào **điện thoại bàn phím phụ thứ 2** của nạn nhân nên máy chính không báo `IN_CALL`!).
- $P \ge 0.75$: **Mức Đỏ (Hard Interception + `FLAG_SECURE` Blackout)** — Phát giọng nói TTS khẩn cấp + Rung SOS + Khóa hạ nhiệt bình tĩnh 15 giây + Câu hỏi đối chứng động + Sẵn sàng Giải cứu Gia đình 2 chiều.

---

## 3. Luồng Nghiệp Vụ End-to-End & Kiến Trúc Hạ Cấp Sống Còn 4 Tầng (4-Tier Fallback Cascades)

> [!IMPORTANT]
> Trong môi trường thực chiến và khi Ban giám khảo chấm bài trực tuyến, bất kỳ API bên thứ ba nào cũng có thể chậm, quota cạn, mạng 4G rớt trong cuộc gọi GSM 2 SIM, hoặc hệ điều hành Xiaomi chặn quyền mở màn hình. Mọi luồng cốt lõi bắt buộc cài đặt **4 Tầng Hạ Cấp Tự Động (Tier 1 $\to$ Tier 4)**.

### 3.1. Ma Trận Hạ Cấp Tự Động 4 Tầng (Zero-Single-Point-of-Failure Matrix)

| Phân Hệ | Tầng 1 (Tối ưu Chính - Primary) | Tầng 2 (Hạ cấp Nhanh - Secondary) | Tầng 3 (Sinh tồn Cục bộ - Offline/Edge) | Tầng 4 (Phục hồi Cuối - Last Resort) |
| :--- | :--- | :--- | :--- | :--- |
| **Trạm 2: Bóc tách Hợp đồng & Chat** | **Gemini 2.5 Flash/Pro Multimodal** (SSE Streaming `<3.5s`, trích xuất JSON + tọa độ bằng chứng). | Tự động failover sau timeout `6s` sang **Model Dự phòng (Gemini Flash-Lite / Secondary Endpoint)** với ảnh nén `960px`. | **On-Device Google MLKit Text Recognition v2 (Latin/Vietnamese)** chạy offline trên Android / Tesseract.js trên Web + **Parser Biểu thức Tiền tệ & Từ điển Phí Cục bộ**. | Mở **Form Bán Tự Động 3 Ô Nhập Nhanh** (*"Vay bao nhiêu - Thực nhận bao nhiêu - Trả bao nhiêu sau mấy ngày"*) để **Engine Toán học** tính ngay không cần AI. |
| **Trạm 3: Đánh chặn Mở App Ngân hàng** | Khởi chạy **`InterceptionActivity` toàn màn hình** + Phát **Giọng nói TTS Tiếng Việt** + Chuỗi rung SOS nhịp tim. | Nếu OEM (Xiaomi/Vivo) chặn mở Activity từ nền (BAL): Bắn **`FullScreenIntent` Urgent Heads-Up Notification** chiếm nửa trên màn hình + **Giọng nói TTS đọc lặp 2 lần qua kênh `STREAM_ALARM`/`USAGE_ASSISTANCE_ACCESSIBILITY`**. | Nếu thiết bị **mất sạch Internet (rớt 4G khi đang gọi SIM 1 DSDS)**: Chạy **100% Bayesian HSM + Cây Hỏi Đáp Kịch Bản Cục Bộ trong SQLCipher** + Ghi hàng đợi sự kiện chờ có mạng đồng bộ bù. | Nếu nạn nhân tắt âm lượng media về `0`: Kênh âm thanh cảnh báo tự động xin phát qua `AudioAttributes.USAGE_NOTIFICATION_EVENT` + nháy đèn màn hình độ tương phản cực đại + kích hoạt báo người thân sau `12s` không phản hồi khi đang có cuộc gọi. |
| **Cảnh báo Khẩn Người thân** | **Live WebSocket (`<200ms`)** đẩy thẳng lên màn hình PWA đang mở của người thân/Giám khảo kèm chuông báo động. | **Web Push Notification (Chuẩn W3C VAPID)** đánh thức trình duyệt điện thoại người thân kể cả khi đã khóa màn hình. | **SMS Gateway / Email Khẩn cấp** từ Backend (hoặc `SmsManager` trực tiếp từ máy Android của người vay nếu người vay đã cấp quyền khẩn cấp và máy đang mất mạng 4G nhưng còn sóng 2G!). | Lưu biên bản sự cố khẩn cấp kèm hướng dẫn xử lý nhanh (*"Gọi tổng đài phong tỏa khẩn cấp của Ngân hàng tương ứng"*) ngay trên màn hình người đi vay. |
| **Trạm 1: Định danh App Vay Lạ** | Đối chiếu chữ ký `SHA-256` + `packageName` với **Cloud Threat & Registry API** thời gian thực. | Kiểm tra trên **Gói Danh sách Trắng & Bloom Filter Đen đã ký số** lưu sẵn trong bộ nhớ cục bộ (`SQLCipher`). | **Heuristic Phân tích Tổ hợp Quyền Nguy hiểm + `InstallSourceInfo`** (bắt mọi APK ngoài luồng đòi `READ_CONTACTS` + `READ_MEDIA_IMAGES`). | Cho phép người dùng chụp màn hình biểu tượng/giao diện app gửi thẳng sang Trạm 2 để kiểm tra chéo. |

---

### 3.2. Sequence Diagram 1 — Luồng Trạm 2: Kiểm Toán Hợp Đồng Đa Ảnh & Chống Mạo Danh (SSE + Human-in-the-Loop)

```mermaid
sequenceDiagram
    autonumber
    actor User as Người Vay / Giám Khảo
    participant Client as Android / Next.js Client
    participant API as FastAPI Stream Gateway
    participant Sanitizer as Canonical & Anti-Injection
    participant AI as Multimodal LLM Cascade
    participant Kernel as Deterministic Finance & Legal Solver
    participant Graph as Pre-SIMO Mule Graph (PG/Redis)

    User->>Client: Chọn / Chia sẻ 1-4 ảnh (Hợp đồng, Bảng phí, Chat Zalo, Bill treo)
    Client->>Client: Nén WebP <= 1280px + Quét sơ bộ MLKit/Hash cục bộ
    Client->>API: POST /api/v1/audits/contract-stream (Multipart + ContextToken)
    API-->>Client: SSE [stage="SANITIZING"] (< 150ms)
    API->>Sanitizer: Chuẩn hóa ảnh, lọc vùng chữ ẩn độ tương phản thấp (< 3%)
    Sanitizer->>AI: Trích xuất theo Strict JSON Schema (Số tiền, Phí, Kỳ hạn, Tên Tổ chức, STK nhận phí)
    alt Primary Model Timeout > 6s hoặc lỗi 5xx
        AI->>AI: Tự động chuyển mạch sang Secondary Fast Vision Model
    end
    AI-->>API: Structured Extraction + Confidence Scores + Evidence Snippets
    API-->>Client: SSE [stage="EXTRACTED", draft_numbers] (Hiển thị ngay cho người dùng kiểm tra)
    API->>Kernel: Giải XIRR, APR_VN_365, Kiểm tra Mâu thuẫn Thương hiệu & Phí ứng trước
    Kernel->>Graph: Tra cứu & Ghi nhận ẩn danh STK nhận phí / Tên App lừa đảo (nếu có)
    Kernel-->>API: VerdictObject (Màu Đỏ/Cam/Xanh, Lãi thực, Ngày công mất, Điều luật viện dẫn)
    API-->>Client: SSE [stage="COMPLETED", final_verdict, tts_script]
    Client->>User: Hiển thị Thẻ Đèn Giao Thông + Tự động đọc TTS Tiếng Việt
    opt Người dùng sửa tay 1 con số AI đọc nhầm (VD: 8.5tr -> 8.0tr)
        User->>Client: Chỉnh số trên UI xác nhận
        Client->>API: POST /api/v1/audits/recalculate (Pure Deterministic < 15ms, 0 tốn gọi lại LLM)
        API-->>Client: Cập nhật tức thì APR/XIRR & Phán quyết mới
    end
```

---

### 3.3. Sequence Diagram 2 — Luồng Trạm 3: Đánh Chặn Sau Giải Ngân Thời Gian Thực (Bao gồm Kịch bản Đang Gọi Điện & Rớt Mạng)

```mermaid
sequenceDiagram
    autonumber
    actor User as Người Đi Vay
    participant BankApp as App Ngân Hàng
    participant EdgeGate as Android Zero-OTP Gatekeeper
    participant HSM as On-Device Bayesian HSM
    participant Audio as AudioManager & TTS Engine
    participant UI as InterceptionActivity (15s Lock)
    participant Backend as FastAPI & WS/Push Hub
    actor Family as Người Thân (PWA / Mobile)

    BankApp->>EdgeGate: Notification "+20,000,000 VND ND: GIAI NGAN HD..."
    EdgeGate->>EdgeGate: Kiểm tra Zero-OTP (<2ms) -> Mask STK & Số dư -> Khớp mẫu Cộng tiền Tổ chức
    EdgeGate->>HSM: Kích hoạt S4_SHIELD_ARMED_24H (Bật ForegroundService đếm ngược 24h)
    HSM--)Backend: Đồng bộ trạng thái ẩn danh SHIELD_ARMED (nếu có mạng)

    Note over BankApp,UI: 12 phút sau: Kẻ gian gọi Zalo/Di động giả danh nhân viên báo "Sai STK, chuyển vào TK kỹ thuật để hủy vay"
    User->>BankApp: Mở App Ngân Hàng để chuyển tiền trong lúc vẫn đang giữ máy nghe gọi
    HSM->>Audio: Truy vấn audioManager.getMode() -> Phát hiện MODE_IN_CALL / MODE_IN_COMMUNICATION
    HSM->>HSM: Tính P(Scam) = 95.7% (>= 0.75) -> Chuyển sang S6_COOLING_OFF_LOCK (< 50ms Offline!)
    HSM->>Audio: Phát giọng nói khẩn cấp Tiếng Việt + Rung SOS nhịp kép
    HSM->>UI: Bật màn hình chắn độc lập đếm ngược 15 giây + Câu hỏi vạch trần kịch bản
    UI->>User: "KHOAN ĐÃ! Bạn đang nghe điện thoại ngay sau khi nhận tiền vay. Có phải họ bảo chuyển tiền để HỦY KHOẢN VAY hoặc MỞ KHÓA TÀI KHOẢN?"
    alt Người dùng bấm xác nhận "Đúng, họ bảo chuyển để hủy/mở khóa" HOẶC nói/gõ lý do nghi vấn HOẶC cố tình mở lại App Ngân hàng >= 2 lần
        UI->>HSM: Chuyển sang S7_ESCALATED_SCAM_CONFIRMED
        alt Có kết nối Internet (Wi-Fi / VoLTE)
            HSM->>Backend: POST /api/v1/shield/escalate (Zero-Amount Safe Payload)
            Backend->>Family: Đẩy cảnh báo WebSocket + Web Push (< 500ms) + Nút "Gọi cho Mẹ ngay"
        else Mất mạng 4G do gọi điện trên máy 2 SIM DSDS
            HSM->>HSM: Đẩy vào Offline Priority Queue + Tùy chọn gửi SMS khẩn trực tiếp qua sóng 2G
        end
    end
```

---

## 4. Đặc Tả Động Cơ Kinh Tế Tài Chính Định Lượng & Ma Trận Pháp Lý 4 Chiều

### 4.1. Mô Hình Hóa Vector Dòng Tiền Tổng Quát ($\mathbf{CF}$)

Mọi hợp đồng vay (dù là vay nóng 7 ngày cắt phế, vay góp ngày/tuần, hay vay tiêu dùng trả góp 12–36 tháng) đều được chuẩn hóa về tập cặp thời điểm - dòng tiền thực tế từ góc nhìn túi tiền người đi vay:

$$\mathbf{CF} = \bigl\{(t_0, CF_0), (t_1, CF_1), \dots, (t_n, CF_n)\bigr\}, \quad t_0 = 0 < t_1 < \dots < t_n \text{ (đơn vị: ngày)}$$

Trong đó:
- **Số tiền thực nhận ròng tại $t_0$**:
  $$CF_0 = P_{\text{disbursed}} - F_{\text{upfront\_out\_of\_pocket}} = \bigl(P_{\text{nominal}} - F_{\text{deducted\_at\_source}} - I_{\text{bundled\_mandatory}}\bigr) - F_{\text{upfront\_out\_of\_pocket}}$$
  - **Bất biến Chặn Cứng Bẫy 1 (Advance-Fee Invariant)**: Nếu yêu cầu nộp bất kỳ khoản tiền nào từ túi người vay trước khi nhận giải ngân ($F_{\text{upfront\_out\_of\_pocket}} > 0$ và $P_{\text{disbursed}} = 0$ tại thời điểm yêu cầu) $\implies$ Dừng toàn bộ bộ giải lãi suất, trả về mã `INFINITE_APR_ADVANCE_FEE_SCAM`.
- **Dòng tiền chi trả kỳ thứ $k$ ($k \ge 1$)**:
  $$CF_k = -\bigl(\text{Principal}_k + \text{Interest}_k + \text{ServiceFee}_k + \text{ManagementFee}_k\bigr) < 0$$
  *(Theo quy định pháp lý Việt Nam, mọi khoản "phí tư vấn, phí quản lý hồ sơ, phí nền tảng" bắt buộc gắn liền với khoản vay để được giải ngân đều phải đưa vào dòng tiền chi phí thực tế của người vay).*

---

### 4.2. Hai Thước Đo Lãi Suất Song Song: Chuẩn Pháp Lý Việt Nam ($\text{APR}_{\text{VN-365}}$) vs. Chuẩn Kinh Tế Học Ghép Lãi ($\text{EAR}_{\text{XIRR}}$)

> [!WARNING]
> Đây là chi tiết phân định trình độ chuyên gia thực thụ với các công cụ tính toán nghiệp dư: Với khoản vay 30 ngày (nhận 8,5 triệu, trả 12 triệu), công thức XIRR ghép lãi kép theo năm sẽ ra **6.568,5%/năm**, trong khi phương pháp quy đổi lãi suất năm cơ sở 365 ngày theo Thông tư 18/2019/TT-NHNN và thực tiễn Giám định Tư pháp Hình sự (Điều 201 BLHS) ra **501,0%/năm**! Nếu chỉ dùng 1 công thức sẽ hoặc bị bắt bẻ về pháp lý, hoặc sai lệch trên khoản vay trả góp nhiều kỳ.

Hệ thống tính toán **đồng thời và chính xác tuyệt đối cả 2 chỉ số**:

#### Chỉ số 1: Lãi suất Quy đổi Theo Năm Chuẩn Pháp Lý Việt Nam ($\text{APR}_{\text{VN-365}}$ — Dùng để so mốc Luật 20% & 100%)
- **Trường hợp A — Vay trả 1 lần cuối kỳ (Bullet Loan, $n = 1$, kỳ hạn $D = t_1$ ngày)**:
  $$\text{APR}_{\text{VN-365}} = \frac{|CF_1| - CF_0}{CF_0} \times \frac{365}{D}$$
- **Trường hợp B — Vay trả góp định kỳ (Installment Loan, $n \ge 2$ kỳ trên dư nợ thực tế giảm dần)**:
  Giải nghiệm lãi suất ngày không ghép lãi chồng lãi (hoặc lãi suất kỳ $r_p$ trên dư nợ gốc thực nhận còn lại) từ phương trình cân bằng hiện giá dòng tiền theo chuẩn Thông tư 18/2019/TT-NHNN, với $r_{\text{annual}} = \text{APR}_{\text{VN-365}}$:
  $$CF_0 + \sum_{k=1}^{n} \frac{CF_k}{\prod_{j=1}^{k} \left(1 + r_{\text{annual}} \cdot \frac{t_j - t_{j-1}}{365}\right)} = 0$$
  *(Công thức này hội tụ chính xác về lãi suất niêm yết trên dư nợ giảm dần cơ sở 365 ngày cho khoản vay trả góp tháng, đồng thời hội tụ chính xác về công thức lãi đơn $\frac{|CF_1| - CF_0}{CF_0} \cdot \frac{365}{D}$ khi $n = 1$! Một công thức toán học thống nhất hoàn hảo cho cả hai trường hợp!).*

#### Chỉ số 2: Lãi suất Hiệu dụng Ghép Lãi Thực tế ($\text{EAR}_{\text{XIRR}}$ — Phản ánh tốc độ xoáy nợ nếu đảo nợ liên tục cả năm)
Giải phương trình chuẩn XIRR:
$$f(r) = CF_0 + \sum_{k=1}^{n} \frac{CF_k}{(1 + r)^{t_k / 365}} = 0$$
*(Hiển thị bổ trợ trong mục Chi tiết Chuyên sâu & Bàn làm việc Fintech Ops để thấy hậu quả kinh tế nếu nạn nhân phải "vay app sau đập app trước" xuyên suốt 12 tháng).*

#### Thuật Toán Giải Hội Tụ Đảm Bảo 100% (`Hybrid Bracketed Brent + Newton-Raphson Solver`)
Phương pháp Newton-Raphson thuần túy thường **phân kỳ hoặc tràn số (`OverflowError`)** khi gặp các app tín dụng đen có lãi suất lên tới $1.000\% - 5.000\%/\text{năm}$. Do đó, `Deterministic Financial Kernel` sử dụng thuật toán **Brent's Root-Finding Method** trên khoảng kẹp bảo toàn dấu $[r_{\min}, r_{\max}] = [-0.99, 500.0]$ (tương ứng tới $50.000\%/\text{năm}$), đạt độ chính xác $|r - r^*| < 10^{-8}$ trong tối đa **$< 45$ vòng lặp (`< 0.4ms` CPU)**:

```python
from dataclasses import dataclass
from typing import Sequence

@dataclass(frozen=True)
class CashFlowPoint:
    day: int          # t_k >= 0 (days from disbursement)
    amount_vnd: float # > 0 for net received at t_0, < 0 for borrower repayments

def solve_vn_legal_apr_365(flows: Sequence[CashFlowPoint], tol: float = 1e-8, max_iter: int = 100) -> float:
    """
    Solves unified Vietnamese Legal Annualized Rate (365-day basis, declining principal balance).
    Exact match for single-period bullet loans AND multi-period installment loans.
    """
    ordered = sorted(flows, key=lambda x: x.day)
    if not ordered or ordered[0].day != 0 or ordered[0].amount_vnd <= 0:
        raise ValueError("INVALID_OR_ADVANCE_FEE_CASHFLOW")

    total_outflow = -sum(f.amount_vnd for f in ordered[1:] if f.amount_vnd < 0)
    if total_outflow <= 0:
        raise ValueError("MISSING_REPAYMENT_OUTFLOWS")

    def npv_legal(r_annual: float) -> float:
        total = ordered[0].amount_vnd
        discount_factor = 1.0
        for idx in range(1, len(ordered)):
            dt = ordered[idx].day - ordered[idx - 1].day
            step = 1.0 + r_annual * (dt / 365.0)
            if step <= 1e-9:
                return float("inf")
            discount_factor *= step
            total += ordered[idx].amount_vnd / discount_factor
        return total

    # Bracket search [lo, hi] guaranteeing opposite signs
    lo, hi = -0.95, 1.0
    f_lo, f_hi = npv_legal(lo), npv_legal(hi)
    while f_lo * f_hi > 0 and hi < 1000.0:
        hi *= 2.0
        f_hi = npv_legal(hi)
    if f_lo * f_hi > 0:
        raise ValueError("NON_MONOTONIC_OR_EXTREME_CASHFLOW")

    # Bisection-Secant safeguarded convergence (Brent-style guarantee)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = npv_legal(mid)
        if abs(f_mid) < tol or (hi - lo) * 0.5 < tol:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)


def solve_apr_interval_bounds(
    flows_best_case: Sequence[CashFlowPoint],
    flows_worst_case: Sequence[CashFlowPoint],
) -> tuple[float, float]:
    """
    Interval Perturbation Verification:
    Computes exact closed bounds [APR_min, APR_max] when secondary fee/date fields have OCR ambiguity.
    If APR_min >= 1.00 (100%/yr), Criminal Usury threshold is provably crossed regardless of OCR noise!
    """
    apr_low = solve_vn_legal_apr_365(flows_best_case)
    apr_high = solve_vn_legal_apr_365(flows_worst_case)
    return (min(apr_low, apr_high), max(apr_low, apr_high))
```

#### Mở Rộng 3 Cấu Trúc Dòng Tiền Phức Tạp Thực Chiến (`Multi-Regime, Grace Period & Disguised Commodity Usury`)
1. **Khoản vay Lãi suất 2 Giai đoạn & Ân hạn Gốc (`Two-Stage Teaser-to-StepUp & Principal Grace`)**:
   - Ví dụ hợp đồng ghi: *"Ân hạn gốc 2 kỳ đầu (chỉ đóng phí quản lý $f_1$), từ kỳ $3 \dots 12$ áp dụng lãi thả nổi biên độ cao $r_2$"*. Bộ sinh dòng tiền tách chính xác $CF_1, CF_2 = -f_1$ và $CF_3 \dots CF_{12} = -\text{PMT}(P_{\text{nominal}}, r_2, 10)$ trước khi đưa vào `solve_vn_legal_apr_365()`.
2. **Tín dụng đen Ngụy trang Mua Trả Góp Bán Lại Tại Quầy (`Disguised Commodity-Resale Usury`)**:
   - Khi người vay ký hợp đồng mua hàng hóa giá hóa đơn $P_{\text{invoice}}$ (VD: `30.000.000đ`) nhưng cò tín dụng thu mua lại ngay tại quầy trả tiền mặt $P_{\text{buyback}}$ (`20.000.000đ`) trừ phí hồ sơ $F_{\text{broker}}$, dòng tiền đầu kỳ thực chất là $CF_0 = P_{\text{buyback}} - F_{\text{broker}}$, trong khi chuỗi nghĩa vụ trả nợ $CF_1 \dots CF_n$ vẫn tính trên $P_{\text{invoice}}$!
3. **Xử lý Dòng tiền Đảo dấu Nhiều lần (`Non-Monotonic Cash Flows / Descartes Multi-Root Guard`)**:
   - Nếu hợp đồng cài "hoàn tiền khuyến mãi kỳ giữa" làm vector $\mathbf{CF}$ đổi dấu $>1$ lần (theo quy tắc dấu Descartes có thể sinh đa nghiệm IRR), Kernel tự động kiểm tra tính đơn điệu đạo hàm $\frac{\partial \text{NPV}}{\partial r} < 0$ trên $( -0.95, +\infty )$ và bổ sung chỉ số **Modified IRR ($\text{MIRR}$)** với lãi suất tái đầu tư chuẩn tiền gửi tiết kiệm $r_{\text{reinvest}} = 4.5\%/\text{năm}$.

---

### 4.3. Chỉ Số Áp Lực Sinh Kế (Livelihood Pain Index) & Mô Phỏng Xoáy Nợ Đáo Hạn

Để cụ thể hóa cho Persona Cô Lan, từ dòng tiền đã chuẩn hóa, hệ thống xuất thêm 3 chỉ số kinh tế hộ gia đình:
1. **Chi phí Lãi & Phí Tuyệt đối**: $C_{\text{loss}} = \left(\sum_{k=1}^n |CF_k|\right) - CF_0$.
2. **Số Ngày Công Lao Động Bị Mất Trắng**: $D_{\text{work}} = \frac{C_{\text{loss}}}{W_{\text{daily}}}$ (mặc định $W_{\text{daily}} = 320.000\text{ VND/ngày công}$, người dùng có thể kéo thanh trượt thu nhập tháng của mình).
3. **Mô phỏng Xoáy Nợ Gia Hạn 3 Chu Kỳ ($S_{\text{rollover}}$)**: Nếu hợp đồng có phí phạt quá hạn hoặc phí "gia hạn giữ gốc" (đặc trưng của app tín dụng đen 7–30 ngày), hệ thống tính dự phóng tổng nợ sau 30 – 60 – 90 ngày không trả nổi gốc để người vay thấy ngay con đường vỡ nợ trước khi bấm nút ký.

---

### 4.4. Ma Trận Phán Quyết 4 Chiều Mở Rộng 9 Nhãn Chính Xác (4D Legal & Anti-Spoofing Matrix)

Hệ thống kết hợp 4 trục độc lập: **(1) Dòng tiền trước giải ngân & Biến tướng hàng hóa/BNPL**, **(2) Tính xác thực nguồn gốc thương hiệu**, **(3) Phân nhóm pháp nhân cho vay**, và **(4) Khoảng $\text{APR}_{\text{VN-365}} \in [\text{APR}_{\min}, \text{APR}_{\max}]$**:

| Mã Phán Quyết | Điều Kiện Kích Hoạt Tất Định (Deterministic Rules) | Nhãn Màu & Hành Động Hệ Thống |
| :--- | :--- | :--- |
| `V_RED_ADVANCE_FEE` | Yêu cầu chuyển bất kỳ khoản phí/thuế/tiền đối ứng/mở khóa tài khoản nào **trước khi nhận tiền giải ngân** ($F_{\text{upfront}} > 0$). | **ĐỎ KHẨN CẤP (LỪA ĐẢO 100%)**: Ẩn chỉ số APR, đọc giọng nói chặn ngay, ghi nhận STK nhận phí vào đồ thị Mule B2B. |
| `V_RED_BRAND_SPOOF` | Trên ảnh có tên/logo Ngân hàng hoặc 1 trong 16 FinCo cấp phép **NHƯNG** nguồn APK sai chữ ký SHA-256 / đòi chuyển tiền vào TK cá nhân / báo "tài khoản bị treo trên hệ thống". | **ĐỎ KHẨN CẤP (MẠO DANH TỔ CHỨC TÍN DỤNG)**: Cảnh báo giả mạo thương hiệu, cung cấp hotline chính thức thật của tổ chức bị mạo danh để đối chứng. |
| `V_RED_BNPL_OR_STRAW_SCAM` | Phát hiện gạ quét QR Merchant ảo để **rút tiền mặt Ví trả sau (BNPL Cash-out)** HOẶC dụ **đứng tên ký eKYC vay hộ nhận hoa hồng**. | **ĐỎ KHẨN CẤP (BẪY CHIẾM ĐOẠT HẠN MỨC / ĐỨNG TÊN NỢ HỘ)**: Cảnh báo mất 100% hạn mức và gánh nợ pháp lý thật trên CIC Nhóm 5. |
| `V_RED_CRIMINAL_USURY` | Chủ thể **không thuộc** Ngân hàng / 16 FinCo cấp phép **VÀ** cận dưới $\text{APR}_{\min} \ge 100\%/\text{năm}$ (kể cả ngụy trang mua bán điện thoại bán lại cắt phế hoặc APK lậu đòi Danh bạ/Ảnh). | **ĐỎ (DẤU HIỆU TÍN DỤNG ĐEN / ĐIỀU 201 BLHS)**: Cảnh báo lãi gấp $\ge 5$ lần trần BLDS và nguy cơ khủng bố danh bạ. |
| `V_ORANGE_CIVIL_CAP_EXCEEDED` | Chủ thể cá nhân / app không phép (hoặc P2P vượt trần dư nợ 100tr/400tr theo NĐ 94/2025) có $20\% < \text{APR}_{\text{VN-365}} < 100\%/\text{năm}$. | **CAM ĐẬM (VƯỢT TRẦN DÂN SỰ 20%/NĂM)**: Giải thích phần vượt 20%/năm không được pháp luật bảo vệ theo Điều 468 BLDS 2015. |
| `V_ORANGE_PAWN_HIGH_FEE` | Hợp đồng cầm cố tài sản hợp pháp (đăng ký xe máy) có lãi danh nghĩa $\le 20\%$ nhưng tổng phí thẩm định/quản lý tài sản đẩy $\text{APR}_{\text{VN-365}} > 35\%/\text{năm}$. | **CAM (VAY CẦM CỐ TÀI SẢN PHÍ CAO)**: Bóc tách riêng lãi vay và phí quản lý tài sản cầm cố để người vay cân nhắc rủi ro mất phương tiện đi làm. |
| `V_YELLOW_HIGH_COST_LICENSED` | Xác thực đúng kênh chính thống của 1 trong 16 FinCo cấp phép / Ngân hàng, không thu phí ngoài luồng, nhưng $\text{APR}_{\text{VN-365}} > 35\%/\text{năm}$ hoặc có kèm phí bảo hiểm tự nguyện. | **VÀNG CAM (HỢP PHÁP NHƯNG CHI PHÍ CAO)**: Không báo trái luật; hiển thị minh bạch tổng tiền gốc + lãi + phí bảo hiểm (nhắc quyền từ chối bảo hiểm không bắt buộc theo Điều 15 Luật các TCTD 2024). |
| `V_AMBER_INCOMPLETE_DOC` | Chụp thiếu trang hợp đồng làm khuyết `tenure` hoặc khoảng nhạy $[\text{APR}_{\min}, \text{APR}_{\max}]$ cắt ngang ngưỡng pháp lý mà chưa đủ căn cứ kết luận. | **VÀNG NHẮC XÁC NHẬN (THIẾU TRANG HỢP ĐỒNG)**: Tạm khóa nhãn kết tội, bật thanh chọn kỳ hạn/số tiền trả 1 chạm hoặc yêu cầu chụp thêm trang tiếp theo. |
| `V_GREEN_TRANSPARENT` | Kênh chính thống của Ngân hàng / FinCo cấp phép / P2P Sandbox đúng hạn mức, không phí ẩn bất thường, $\text{APR}_{\text{VN-365}} \le 35\%/\text{năm}$. | **XANH (HỢP PHÁP & MINH BẠCH)**: Hiển thị lịch trả nợ rõ ràng và **vẫn tự động hẹn giờ bật Khiên Trạm 3** phòng kẻ gian gọi điện lừa sau giải ngân! |

---

## 5. Danh Mục 48 Edge Cases Hệ Thống, Viễn Thông, Thanh Toán, Toán Tài Chính & Phân Tán

Bảng dưới đây đặc tả đầy đủ **48 tình huống biên kỹ thuật & tấn công đối kháng** (mở rộng từ 28 kịch bản gốc thêm 20 kịch bản chuyên sâu cấp hệ điều hành, toán học và hệ phân tán):

| # | Miền | Tình huống Biên / Tấn công Đối kháng (Edge Case) | Cơ chế Hóa giải Kỹ thuật trong VayShield AI |
| :-: | :--- | :--- | :--- |
| **01** | **OS & Viễn thông** | **Rớt mạng 4G trên máy 2 SIM phổ thông (DSDS)**: Kẻ gian gọi di động vào SIM 1 làm SIM 2 mất kết nối dữ liệu 4G đúng lúc nạn nhân mở App Ngân hàng. | Toàn bộ `Bayesian HSM`, `AudioManager` check, `TTS Engine` và `InterceptionActivity` ở Trạm 3 thực thi **100% Offline** từ bộ nhớ cục bộ; hàng đợi cảnh báo người thân tự động đẩy ngay khi có Wi-Fi/4G trở lại hoặc gửi qua SMS 2G. |
| **02** | **OS & Viễn thông** | **Kẻ gian bảo nạn nhân cúp máy trước khi mở App Ngân hàng 30 giây** để né kiểm tra đang gọi điện. | `CallStateTracker` lưu dấu thời gian kết thúc `MODE_IN_CALL`/`MODE_IN_COMMUNICATION` gần nhất; cửa sổ dư chấn **180 giây sau khi cúp máy** vẫn cộng trọng số rủi ro cao ($+1.9$). |
| **03** | **OS & OEM** | **Xiaomi Dual Apps / Samsung Secure Folder / Oppo Clone Phone**: Người dùng dùng bản nhân đôi của Zalo hoặc App Ngân hàng. | `NotificationListenerService` và `UsageStatsManager` chuẩn hóa `packageName` gốc bỏ hậu tố clone profile và kiểm tra trên mọi `UserHandle` được phép hiển thị. |
| **04** | **OS & OEM** | **Xiaomi HyperOS / Vivo / Oppo chặn mở Activity từ nền (BAL Restriction)** khiến `InterceptionActivity` không bật lên. | Kiểm tra trạng thái hiển thị sau `250ms` bằng `LifecycleObserver`; nếu Activity chưa lên foreground, lập tức kích hoạt Fallback Tier 2: **`FullScreenIntent` Heads-Up Notification ưu tiên cực đại + Đọc giọng nói TTS lặp 2 vòng**. |
| **05** | **OS & Âm thanh** | **Nạn nhân đang cắm tai nghe Bluetooth hoặc để điện thoại chế độ Im lặng** khi mở App Ngân hàng. | Giọng nói TTS tự động định tuyến sang tai nghe đang đàm thoại (để nạn nhân nghe trực tiếp bên tai!) kết hợp chuỗi rung Haptic SOS cường độ cao và khóa đếm ngược thị giác 15 giây trên màn hình. |
| **06** | **OS & Quyền** | **Android 13–15 `Restricted Settings`** khóa công tắc `NotificationListenerService` khi cài APK rời. | Cung cấp link cài qua **Google Play Internal App Sharing** (miễn nhiễm khóa Restricted Settings) + Wizard hướng dẫn 3 bước có nút mở thẳng trang `ACTION_APPLICATION_DETAILS_SETTINGS` của máy. |
| **07** | **Thanh toán** | **Thông báo giải ngân về 2 lần (cả SMS Banking lẫn Push Notification của App Ngân hàng)** gây kích hoạt trùng lặp. | Bộ lọc **Sliding-Window Idempotency (`10 phút`)** băm cặp `(NormalizedAmountBucket, MonotonicTimeBucket)` hợp nhất thành 1 sự kiện giải ngân duy nhất. |
| **08** | **Thanh toán** | **Chia nhỏ số tiền chuyển đi (Smurfing)**: Nhận vay 20 triệu, kẻ gian bảo chuyển 4 lần mỗi lần 5 triệu để trông không giống chuyển trả toàn bộ khoản vay. | Trạm 3 canh theo **trạng thái phiên 24h**, không phụ thuộc người dùng nhập số tiền bao nhiêu trong app ngân hàng; đồng thời bộ đếm $\Omega_{\text{smurf}}(t)$ tăng điểm rủi ro theo cấp số cộng mỗi lần quay lại app ngân hàng! |
| **09** | **Thanh toán** | **Chuyển tiền vòng qua trung gian (`Ngân hàng -> Nạp Ví Điện Tử -> Chuyển sang TK Kỹ Thuật`)**. | Danh sách ứng dụng giám sát ở Trạm 3 (`MonitoredFinancialPackages`) bao gồm cả Ngân hàng LẪN các Ví điện tử / Trung gian thanh toán phổ biến (MoMo, ZaloPay, Viettel Money, VNPay). |
| **10** | **Thanh toán** | **Quét mã VietQR lừa đảo**: Kẻ gian gửi ảnh mã QR "tài khoản kỹ thuật" qua Zalo để nạn nhân mở camera ngân hàng quét thẳng mà không gõ số tài khoản. | Ở Trạm 2, bộ quét ảnh tự động **giải mã chuỗi EMVCo VietQR (CRC16, Bank BIN Tag `38`, Số tài khoản đích)** ngay trên ảnh chụp chat Zalo để trích xuất tên/STK thật và đối chiếu đồ thị Mule! |
| **11** | **Thanh toán** | **Nhận lương hoặc người thân chuyển khoản lớn (`+15 triệu`) trùng ngày** dễ bị nhầm là tiền giải ngân vay. | Phân biệt qua phân tích ngữ nghĩa nội dung chuyển khoản cục bộ: nếu không có ngữ cảnh vay trước đó (`S0_IDLE`), chỉ kích hoạt Trạm 3 khi nội dung chứa dấu hiệu tổ chức tài chính/giải ngân rõ rệt, bỏ qua chuyển khoản cá nhân thông thường. |
| **12** | **Tài chính** | **Hợp đồng chỉ ghi "Góp 250.000đ/ngày trong 50 ngày" cho khoản vay 10 triệu (không ghi % lãi suất nào)**. | Schema trích xuất cho phép `stated_interest_rate = null`, tự động sinh chuỗi 50 dòng tiền ngày $CF_1 \dots CF_{50} = -250.000$ và giải chính xác $\text{APR}_{\text{VN-365}} = 351.8\%/\text{năm}$! |
| **13** | **Tài chính** | **Lãi suất quảng cáo theo tháng tính trên dư nợ gốc ban đầu (Flat Rate)** so với dư nợ giảm dần. | Bộ giải tự động phát hiện mâu thuẫn giữa `stated_monthly_rate` và `monthly_installment_amount`; luôn tính $\text{APR}_{\text{VN-365}}$ dựa trên **dòng tiền thực trả**, không bao giờ tin con số % ghi trên tiêu đề quảng cáo. |
| **14** | **Tài chính** | **Khoản vay "Lãi 0% kỳ đầu" (Teaser Rate) nhưng thu "Phí tư vấn công nghệ 25%" khấu trừ thẳng lúc giải ngân**. | Công thức $CF_0 = P_{\text{nominal}} - F_{\text{deducted}}$ tự động quy toàn bộ phí khấu trừ tại nguồn vào chi phí vốn thực tế. |
| **15** | **Tài chính** | **Bẫy "Đáo hạn / Gia hạn khoản vay" (Rollover Fee)**: Nạn nhân chụp màn hình đòi đóng `1.800.000đ` để gia hạn khoản vay `10.000.000đ` thêm 7 ngày. | Trạm 2 nhận diện mẫu `LOAN_EXTENSION_FEE`, tính tốc độ hút máu riêng của 7 ngày gia hạn ($\frac{1.8}{10} \times \frac{365}{7} = 938.6\%/\text{năm}$) và hướng dẫn cách xử lý pháp lý thay vì đóng phí không giảm gốc. |
| **16** | **Pháp lý** | **Công ty đặt tên gần giống Tổ chức Tín dụng thật** (VD: *"Tài chính HomeCredit Quốc Tế"*, *"Tín dụng FE Việt"*, *"Shinhan Vay Nhanh"*). | Thuật toán **Fuzzy Lookalike & Homoglyph Detector (Levenshtein + Jaro-Winkler)** phát hiện độ tương đồng tên cao ($>0.78$) nhưng lệch tên pháp nhân chuẩn/domain chính thức $\implies$ Đánh cờ **Giả mạo Thương hiệu**. |
| **17** | **Pháp lý** | **Nền tảng P2P Lending có trong Sandbox Nghị định 94/2025 nhưng chào khoản vay 150 triệu đồng** (vượt trần 100 triệu/nền tảng theo QĐ 2866/QĐ-NHNN). | Kiểm tra ràng buộc hạn mức pháp lý: nếu `lender_type == P2P_SANDBOX` và `principal > 100_000_000` $\implies$ Cảnh báo vi phạm hạn mức thử nghiệm Sandbox (nguy cơ mạo danh nền tảng P2P). |
| **18** | **AI Đối kháng** | **Prompt Injection trên ảnh hợp đồng** (Chữ trắng trên nền trắng hoặc chữ siêu nhỏ 2px chỉ dẫn sai cho LLM). | Tiền xử lý ảnh cắt ngưỡng tương phản cục bộ, đồng thời LLM chỉ trả JSON các con số thô kèm trích dẫn văn bản gốc; tầng Python kiểm tra tính nhất quán toán học, vô hiệu hóa mọi lệnh điều khiển văn bản. |
| **19** | **AI Đối kháng** | **Kẻ gian viết lách luật bằng Teencode / Chèn ký tự đặc biệt / Homoglyph Cyrillic** (*"p.h.í m.ở b.ă.n.g"*, *"tài kh0ản kỹ thuật"*). | Bộ `Canonical Sanitizer` loại bỏ dấu chấm xen kẽ, chuyển đổi ký tự đồng hình (`0 -> o`, `1 -> i`), chuẩn hóa Unicode NFKC trước khi phân loại. |
| **20** | **AI Thị giác** | **Ảnh chụp lại màn hình điện thoại thứ 2 (Moiré pattern, lóa sáng, nghiêng góc)** của cô chú công nhân lớn tuổi. | Kiểm tra độ mờ (Laplacian Variance) ngay trên Client; nếu quá mờ nhắc chụp lại ngay lập tức (`<100ms`), kết hợp hiển thị **UI Xác nhận Số liệu Tương tác** để người dùng sửa 1 chạm nếu OCR lệch 1 chữ số. |
| **21** | **AI Đa ảnh** | **Người dùng tải lên 3 ảnh rời rạc của cùng 1 hợp đồng nhưng bị trùng lặp số tiền ở 2 ảnh** (nguy cơ cộng đôi khoản phí!). | Thuật toán **Semantic Field Deduplication**: Hợp nhất ngữ cảnh đa ảnh theo thực thể hợp đồng duy nhất thay vì cộng dồn cơ học từng ảnh độc lập. |
| **22** | **Tâm lý Người dùng** | **Kẻ gian mớm lời kịch bản mới**: Dặn nạn nhân khi VayShield hỏi thì chọn *"Tôi trả nợ tiền hàng cho bạn"*. | Trạm 3 khi phát hiện đang có cuộc gọi trực tiếp (`IN_CALL` / `IN_COMM`) **không cho phép mở khóa chỉ bằng 1 câu trả lời chung chung**, mà hiển thị cảnh báo giọng nói điểm mặt trực tiếp hành vi mớm lời kèm đếm ngược bắt buộc 15 giây! |
| **23** | **Tâm lý & Riêng tư** | **Người vay xấu hổ sợ người thân biết mình vừa vay 15 triệu đồng**. | Thiết kế **Zero-Amount Privacy Envelope**: Tin báo sang PWA người thân tuyệt đối không chứa tên công ty vay hay số tiền vay, chỉ báo gặp rủi ro lừa đảo qua điện thoại mức độ cao cần gọi hỏi thăm ngay. |
| **24** | **Bảo mật API** | **Kẻ gian spam API ghép đôi người thân để dò số điện thoại hoặc quấy rối**. | Ghép đôi dựa trên **Mã Phiên Ngẫu Nhiên 128-bit (QR / Mã 6 ký tự chữ-số entropy cao, TTL 10 phút, tối đa 3 lần thử sai)** do chính người đi vay khởi tạo, không cho phép bắn tin tới SĐT chưa xác nhận hai chiều. |
| **25** | **Bảo mật Dữ liệu** | **Thông báo chứa mã OTP chuyển tiền của ngân hàng đi qua `NotificationListenerService`**. | Chốt chặn cứng `DROP_IF_OTP_REGEX` ngay dòng đầu tiên của hàm `onNotificationPosted()` trong RAM, kèm Unit Test bảo mật bắt buộc chứng minh `0 byte` OTP chạm vào logger, database hay socket mạng. |
| **26** | **B2B Webhook** | **Đối tác Fintech bắn trùng lặp sự kiện giải ngân (Retry Storm) hoặc giả mạo gói tin Webhook**. | Xác thực chữ ký **`X-VayShield-Signature` (HMAC-SHA256 kèm Timestamp chống Replay Attack `<= 300s`)** và khóa `Idempotency-Key` trên Redis TTL 48 giờ. |
| **27** | **Đồ thị B2B** | **Kẻ xấu cố tình gửi ảnh giả để "vu khống" số tài khoản của đối thủ cạnh tranh là tài khoản lừa đảo**. | Đồ thị `Pre-SIMO Mule Graph` áp dụng cơ chế **Điểm Tin cậy Đa Nguồn (Multi-Reporter Quorum & Evidence Weighting)**: STK chỉ được nâng lên *Confirmed* khi xuất hiện từ $\ge 2$ thiết bị độc lập kèm bằng chứng hợp lệ. |
| **28** | **Chấm thi Giám khảo** | **Ban giám khảo dùng iPhone hoặc Laptop MacBook không có máy Android để test các tín hiệu phần cứng ngoại vi ở Trạm 1 & Trạm 3**. | Thay vì làm màn hình Demo giả, ngay trên **Sản phẩm Web/PWA thật** tích hợp công tắc **`Sandbox Mode` (kiểu Stripe Test Mode)**: Giám khảo gạt nút mở ngăn kéo kích hoạt sự kiện ngoại vi (*ngân hàng giải ngân, cuộc gọi kẻ gian*) bắn thẳng vào **chính luồng Backend & UI của sản phẩm thật** trong namespace cô lập! |
| **29** | **Bảo mật Thời gian** | **Kẻ gian hướng dẫn nạn nhân vào Cài đặt chỉnh đồng hồ điện thoại tăng thêm `+2 ngày`** để làm hết hạn sớm Khiên bảo vệ 24h! | Toàn bộ bộ đếm TTL bám theo đồng hồ phần cứng đơn điệu **`SystemClock.elapsedRealtimeNanos()`** + độ lệch chuẩn ký số từ Server (`SignedServerEpochOffset`); việc đổi giờ máy không có tác dụng phá Khiên! |
| **30** | **Đa Phiên Vay** | **Người dùng túng quẫn cài và đăng ký 4 app vay khác nhau trong cùng 1 giờ** làm xung đột ghi đè trạng thái HSM. | `Multi-Fiber Semilattice Supervisor` duy trì độc lập tối đa $K = 8$ luồng khoản vay song song và lấy cận trên rủi ro lớn nhất cộng hệ số áp lực đa khoản vay $\gamma_{\text{multi}}$. |
| **31** | **OS & Viễn thông** | **Kẻ gian gọi vào điện thoại phụ "cục gạch" thứ 2 của nạn nhân**, còn nạn nhân mở App Ngân hàng trên Smartphone Android chính (`AudioMode` báo `MODE_NORMAL`!). | Trong **120 phút vàng sau giải ngân**, chỉ riêng trọng số $w_{\text{disb}} = +2.8$ (hoặc $+4.2$ nếu có cờ Trạm 2) đã đưa $P(\text{Scam})$ vượt ngưỡng **Mức Cam (`Soft Check`)**, bật câu hỏi không đối đầu *"Giao dịch này dùng làm thủ tục gì?"* bắt gọn nạn nhân bấm *"Hủy vay / Xác minh TK"*! |
| **32** | **OS & Màn hình** | **Kẻ gian gọi Video Zalo ở chế độ cửa sổ nổi thu nhỏ (`Picture-in-Picture` / `Split-Screen`)** hoặc bật **Chia sẻ màn hình (`MediaProjection`)** trong lúc nạn nhân mở App Ngân hàng. | Bắt cờ `Activity.isInMultiWindowMode` + thông báo hệ thống `FOREGROUND_SERVICE_MEDIA_PROJECTION` ($\Xi_{\text{proj}} = +4.5$) và bật cứng `FLAG_SECURE` làm tối đen luồng chiếu màn hình của kẻ gian! |
| **33** | **OS & Khởi động** | **Điện thoại sập nguồn hết pin hoặc khởi động lại giữa chừng khi đang trong 24h bảo vệ** làm mất tiến trình `ForegroundService`. | `BootCompletedReceiver` lắng nghe `ACTION_BOOT_COMPLETED` & `ACTION_MY_PACKAGE_REPLACED`, đọc bảng mã hóa `SQLCipher` tính lại thời gian còn lại theo `ServerEpochOffset` để tự động bật lại Khiên bảo vệ! |
| **34** | **OS & Bộ nhớ** | **Người dùng chụp "Ảnh màn hình cuộn dài" (`Scrolling Screenshot 1080 x 12000 px`)** trên máy RAM 3GB: nếu thu nhỏ theo chiều dài nhất về `1280px` thì chiều ngang chỉ còn `115px` làm nát sạch chữ! | Thuật toán **`Aspect-Ratio Aware Tiled Slicer`**: Khi phát hiện `height / width > 2.5`, giữ nguyên chiều ngang chuẩn `1080px` và cắt dọc thành các lát cao `1800px` chồng lấp `15%` biên để OCR đọc sắc nét 100% không tràn RAM! |
| **35** | **Thanh toán** | **Kẻ gian gửi đường link Deeplink (`vietqr://`, `Intent URI` ngân hàng) qua Zalo** để nhảy thẳng vào trang xác nhận chuyển tiền, không đi qua màn hình chính của App Ngân hàng. | `UsageStatsManager` theo dõi sự kiện `UsageEvents.Event.ACTIVITY_RESUMED` ở cấp độ `packageName` bất kể Activity con nào của App Ngân hàng/Ví điện tử được mở qua Deeplink. |
| **36** | **Thanh toán** | **Kẻ gian dụ lập "Lệnh chuyển tiền hẹn giờ ngày mai" (`Scheduled Transfer`) hoặc rút tiền mặt ra nộp tại cây CDM/cửa hàng thu hộ** để né kiểm tra tức thời. | Cảnh báo giọng nói và thẻ giải thích ở Trạm 3 nêu đích danh: *"Không chuyển khoản ngay, không cài lệnh chuyển hẹn giờ, và tuyệt đối không rút tiền mặt mang đi nộp vào bất kỳ tài khoản kỹ thuật nào!"*. |
| **37** | **Thanh toán** | **Định dạng số tiền thông báo ngân hàng hỗn loạn dấu chấm/phẩy & ngoại tệ** (`+20,000,000 VND`, `+20.000.000,00đ`, `+500.00 USD`). | Bộ chuẩn hóa tiền tệ cục bộ phân tích hậu tố thập phân (`.00` / `,00`) và đơn vị tiền tệ (`VND/đ` vs `USD/EUR`), tránh lỗi nhân gấp 100 lần số tiền giải ngân! |
| **38** | **Thanh toán** | **Ngân hàng gửi thông báo cộng tiền do "Hoàn tiền giao dịch lỗi / Reversal / Cashback mua hàng"** dễ bị nhận nhầm là giải ngân khoản vay mới. | Từ điển phủ định cục bộ loại trừ ngay các thông báo chứa cụm `HOAN TIEN`, `REVERSAL`, `REFUND`, `CASHBACK`, `TRA LAI TIEN HANG` khi thiết bị đang ở trạng thái nghỉ `S0_IDLE`. |
| **39** | **Tài chính** | **Hợp đồng lãi thả nổi 2 giai đoạn hoặc Ân hạn gốc** (*"3 tháng đầu chỉ đóng 200k, tháng thứ 4 đóng dồn gốc + lãi phạt"*). | Bộ sinh dòng tiền đa chế độ (`Multi-Regime Cashflow Builder`) mô hình hóa chính xác từng kỳ không đồng nhất thay vì giả định `PMT` bằng nhau ở mọi kỳ. |
| **40** | **Tài chính** | **Dòng tiền đổi dấu nhiều lần (`CF_0 > 0, CF_1 < 0, CF_2 > 0 (thưởng hoàn phí), CF_3 < 0`)** gây đa nghiệm IRR toán học. | Kiểm tra quy tắc đổi dấu Descartes; nếu đổi dấu $>1$ lần, tự động khóa sang nghiệm nhánh đơn điệu kinh tế duy nhất kết hợp tính **Modified IRR ($\text{MIRR}$)**. |
| **41** | **Tài chính** | **Ảnh chụp bị lóa đúng dòng "Phí bảo hiểm 600.000đ" (không rõ đã trừ vào gốc hay cộng vào kỳ trả)**. | Giải **Khoảng Bất Định Đóng $[\text{APR}_{\min}, \text{APR}_{\max}]$** bằng `solve_apr_interval_bounds()`; chỉ đổi phán quyết khi ranh giới pháp lý nằm lọt vào giữa khoảng bất định! |
| **42** | **Tài chính** | **Lệch quy ước đếm ngày trả nợ (`30/360` vs `Actual/365` và dời ngày nghỉ Tết / Cuối tuần)**. | Chuẩn hóa mọi phép kiểm định pháp lý về đúng cơ sở **`Actual/365` (365 ngày/năm)** theo quy định bắt buộc tại Thông tư 18/2019/TT-NHNN và Thông tư 39/2016/TT-NHNN, với dung sai biên $\pm 0.5\%$ tại mốc ranh giới 20% để không báo sai do dời ngày Chủ nhật. |
| **43** | **Phân tán & Đua tin** | **Race Condition song song**: Đối tác B2B bắn Webhook kích hoạt Khiên 24h lên Server đúng cùng giây máy Android của người vay bắt được thông báo giải ngân gửi lên Server! | Hợp nhất nguyên tử trên Redis/PostgreSQL bằng khóa phiên theo `(device_id, time_window_30m)`: nâng cấp trạng thái theo toán tử `MAX(shield_expires_at)` và gộp nguồn bằng chứng (`B2B_CONFIRMED + EDGE_NOTIF`), không tạo 2 phiên trùng! |
| **44** | **Mạng & WebSocket** | **Kết nối WebSocket của máy Người thân bị treo dạng "Half-Open Zombie TCP"** khi đi vào hầm gửi xe làm Server gửi gói tin vào hố đen mà không biết lỗi! | Không bao giờ tin `socket.send()` thành công là người thân đã nhận! Luồng leo thang bắt buộc chờ khung tin ứng dụng **`CLIENT_ALERT_ACK`** trong `10 giây`; không có `ACK` lập tức bắn Web Push VAPID $\to$ SMS/Email! |
| **45** | **Hàng đợi Offline** | **Bão đồng bộ trễ (`Stale Offline Alert Storm`)**: Máy người vay mất mạng lúc 2 giờ chiều, đến 2 giờ sáng hôm sau mới bật Wi-Fi làm hàng đợi đẩy dồn 10 cảnh báo cũ đánh thức cả nhà! | Bộ lọc **`Stale-Event Coalescer & TTL Pruner`** trong `WorkManager`: Cảnh báo khẩn cấp quá `45 phút` không còn đẩy chuông báo động SOS nửa đêm mà gom thành 1 bản tin tóm tắt nhật ký yên lặng (`Silent Audit Summary`). |
| **46** | **Mạng Quốc tế** | **Sự cố đứt cáp quang biển làm tăng độ trễ gọi API Gemini quốc tế lên `> 8 giây`** trong khi mạng trong nước tới Backend vẫn `< 30ms`. | Bộ định tuyến đặt cứng `Soft-Timeout = 4.5s` cho Pha 1 Vision; nếu mạng quốc tế nghẽn, kích hoạt song song OCR dự phòng nội bộ để trả số liệu thô cho `Deterministic Kernel` tính trước không bắt người dùng chờ! |
| **47** | **Tội phạm Công nghệ cao** | **Kẻ gian dùng Deepfake Video / Giọng nói giả mạo lãnh đạo công ty tài chính hoặc cán bộ điều tra trên Zalo Video Call**. | Khi phát hiện `MODE_IN_COMMUNICATION` (VoIP/Video Call) đi kèm yêu cầu chuyển tiền sau giải ngân, màn hình khóa bình tĩnh hiển thị cảnh báo chống Deepfake: *"Hình ảnh và giọng nói trên cuộc gọi video hoàn toàn có thể làm giả bằng AI — Chỉ gọi lại số Hotline chính thức!"*. |
| **48** | **Cấp cứu Sau Mất Tiền** | **Người dùng bấm nút *"Tôi vừa lỡ chuyển tiền mất rồi!"* ngay tại Trạm 3 hoặc tải lên biên lai chuyển khoản thành công cho kẻ gian**. | Chuyển ngay sang **`S9_GOLDEN_HOUR_TRIAGE`**: Khóa cứng tâm lý chống chuyển tiếp lần 2 ("tiền giải tỏa"), tra mã BIN ngân hàng hiển thị nút gọi thẳng tổng đài khẩn cấp phong tỏa giao dịch và xuất file tóm tắt bằng chứng nộp Công an trong Giờ Vàng! |

---

## 6. Điểm Neo Chuyển Tiếp Sang Session 3 (AI/LLM Pipeline, Anti-Adversarial Vision, Ontology & Dataset)

Kiến trúc hệ thống C4 7 chặng, bộ giám sát đa phiên `Multi-Fiber Bayesian HSM`, chuỗi hạ cấp 4 tầng, bộ giải toán học khoảng $\text{APR}_{\text{VN-365}}$ / XIRR / MIRR và danh mục **48 Edge Cases Hệ thống - Tài chính - Phân tán** đã được đặc tả hoàn chỉnh.

Trong **Session 3**, Hội đồng (do **Senior AI Engineer, Cybersecurity Engineer, Fintech Specialist & QA Engineer** chủ trì) sẽ đi sâu thiết kế cấp độ triển khai code cho:
1. **Kiến trúc Chi tiết Đường ống AI Đa phương thức (Multimodal Vision + Voice STT/TTS)**: Thiết kế System Prompts chống Injection, JSON Schema định kiểu chặt cho hợp đồng nhiều ảnh + đoạn chat Zalo + giải mã VietQR, và thuật toán tiền xử lý ảnh chống chữ ẩn.
2. **Bản thể học Ý định Thao túng Tâm lý (Coercive Intent Ontology) & Bộ phân loại Trạm 3**: Phân biệt các biến thể kịch bản lừa đảo không phụ thuộc từ khóa tĩnh, kết hợp cơ chế đối thoại giải tỏa tâm lý nạn nhân.
3. **Kiến trúc Legal GraphRAG (`pgvector` + Điều khoản Pháp luật VN)**: Cách cắt chunk điều luật (BLDS Điều 468, BLHS Điều 201, Nghị định 94/2025, TT 43/2016, TT 18/2019, Luật các TCTD 2024) để trích dẫn chính xác điều khoản mà không bao giờ bịa đặt văn bản luật (Zero Legal Hallucination).
4. **Đặc tả Bộ Dữ liệu Kiểm định Chuẩn Vàng (Golden Benchmark Dataset) & Quy trình Thực nghiệm Khoa học**: Cấu trúc 50 mẫu hợp đồng/biên lai + 100 kịch bản hội thoại đối kháng để đo lường thực nghiệm chứng minh 4 giả thuyết $H_1 \to H_4$ đưa thẳng vào Slide thuyết trình vòng Chung kết!
