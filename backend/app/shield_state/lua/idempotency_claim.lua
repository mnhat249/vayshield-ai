-- idempotency_claim.lua — giữ chỗ khóa idempotency nguyên tử.
-- KEYS[1]: vs:idem:notif:{device_id}:{hash} | vs:idem:b2b:{partner_id}:{key}
-- ARGV[1]: giá trị chủ sở hữu (vd session_id), ARGV[2]: TTL ms
-- Trả về: {1, owner} nếu lần đầu; {0, owner_cũ} nếu trùng.
local existing = redis.call('GET', KEYS[1])
if existing then
    return {0, existing}
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', tonumber(ARGV[2]))
return {1, ARGV[1]}
