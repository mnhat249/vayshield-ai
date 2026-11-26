-- atomic_smurf_and_risk_eval.lua (File 4 §3.2)
-- Trượt cửa sổ 15 phút + đếm lần mở lại app ngân hàng + tính P(Scam) trong 1 thao tác nguyên tử.
--
-- KEYS[1]: vs:smurf:win:{session_id}
-- KEYS[2]: vs:shield:state:{device_id}
-- ARGV[1]: now_epoch_ms (caller cung cấp — không đọc đồng hồ Redis để tất định)
-- ARGV[2]: event_uuid (ZADD lại cùng id = idempotent, không đếm trùng)
-- ARGV[3]: call_synergy_weight (3.8 đang gọi, 1.9 cúp máy <= 180s, 0.0 còn lại)
-- ARGV[4]: (mở rộng, tùy chọn) proj_weight Xi_proj (4.5 khi chia sẻ màn hình / điều khiển từ xa)
-- ARGV[5]: (mở rộng, tùy chọn) base_log_odds mới tính tại now (ghi đè giá trị cũ trong HASH)
-- ARGV[6]: (mở rộng, tùy chọn) TTL ms cho HASH nếu khóa chưa có TTL (mặc định 24h)
-- Trả về: {prob (chuỗi), open_count_15m (số nguyên), total_log_odds (chuỗi)}

local win_key = KEYS[1]
local state_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local window_ms = 15 * 60 * 1000
local cutoff_ms = now_ms - window_ms

-- 1. Xóa các lần mở app ngân hàng cũ hơn 15 phút và ghi nhận lần mở hiện tại
redis.call('ZREMRANGEBYSCORE', win_key, '-inf', cutoff_ms)
redis.call('ZADD', win_key, now_ms, ARGV[2])
redis.call('PEXPIRE', win_key, window_ms)

local open_count_15m = redis.call('ZCARD', win_key)

local base_log_odds
if ARGV[5] ~= nil and ARGV[5] ~= '' then
    base_log_odds = tonumber(ARGV[5])
    redis.call('HSET', state_key, 'base_log_odds', ARGV[5])
else
    base_log_odds = tonumber(redis.call('HGET', state_key, 'base_log_odds') or '-3.5')
end

local synergy = tonumber(ARGV[3]) or 0.0
local proj = 0.0
if ARGV[4] ~= nil and ARGV[4] ~= '' then
    proj = tonumber(ARGV[4]) or 0.0
end

local smurf_boost = 0.0
if open_count_15m > 1 then
    smurf_boost = 0.9 * (open_count_15m - 1)
end

local total_log_odds = base_log_odds + synergy + proj + smurf_boost
local prob
if total_log_odds >= 0 then
    prob = 1.0 / (1.0 + math.exp(-total_log_odds))
else
    local z = math.exp(total_log_odds)
    prob = z / (1.0 + z)
end

redis.call('HSET', state_key, 'last_prob', tostring(prob), 'smurf_count', tostring(open_count_15m))
-- Không để HASH trạng thái sống vô hạn nếu được tạo mới tại đây.
if redis.call('PTTL', state_key) < 0 then
    local ttl = tonumber(ARGV[6] or '') or (24 * 3600 * 1000)
    redis.call('PEXPIRE', state_key, ttl)
end
return {tostring(prob), open_count_15m, tostring(total_log_odds)}
