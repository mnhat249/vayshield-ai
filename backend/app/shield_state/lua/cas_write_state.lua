-- cas_write_state.lua — ghi HASH trạng thái HSM có kiểm phiên bản (chống lost-update, EC-DB07).
-- KEYS[1]: vs:shield:state:{device_id}
-- ARGV[1]: phiên bản kỳ vọng (0 nếu chưa có)
-- ARGV[2]: TTL ms
-- ARGV[3..]: cặp field, value
-- Trả về: phiên bản mới, hoặc -1 nếu xung đột.
local key = KEYS[1]
local expected = tonumber(ARGV[1])
local current = tonumber(redis.call('HGET', key, 'version') or '0') or 0
if current ~= expected then
    return -1
end
local new_version = current + 1
local args = {}
for i = 3, #ARGV do
    args[#args + 1] = ARGV[i]
end
args[#args + 1] = 'version'
args[#args + 1] = tostring(new_version)
redis.call('HSET', key, unpack(args))
redis.call('PEXPIRE', key, tonumber(ARGV[2]))
return new_version
